"""Durable orchestration execution and recovery integration tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all, fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.event_writer import SqliteRunEventWriter
from pangi.adapters.outbound.persistence.sqlite.orchestration_execution import (
    SqliteOrchestrationExecutionStore,
)
from pangi.adapters.outbound.persistence.sqlite.runs import (
    SqliteRunQueueStore,
    SqliteRunStore,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.orchestration import (
    AgentResult,
    AgentResultStatus,
    DelegatedTask,
)
from pangi.application.contracts.orchestration_execution import (
    ExecutionPolicy,
    PreparedExecutionPlan,
    PreparedExecutionStep,
    StepExecutionRequest,
)
from pangi.application.contracts.run_queue import RunClaim, RunQueuePolicy
from pangi.application.ports.orchestration_execution import (
    ExecutionOwnershipLostError,
    ExecutionPlanConflictError,
)
from pangi.application.services.execution_engine import DependencyExecutionEngine
from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)
from pangi.application.services.run_queue import RunQueueService
from pangi.application.services.runs import RunService
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)
from pangi.domain.auth import UserRole
from pangi.domain.runs import (
    Principal,
    PrincipalChannel,
    Run,
    RunErrorCode,
    RunMode,
    RunRequest,
    RunState,
    StepRequirement,
    StepState,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class RecordingExecutor:
    def __init__(
        self,
        *,
        failures: frozenset[str] = frozenset(),
    ) -> None:
        self.failures = failures
        self.calls: list[StepExecutionRequest] = []
        self.active = 0
        self.peak = 0

    async def execute(self, request: StepExecutionRequest) -> AgentResult:
        self.calls.append(request)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            if request.task.id in self.failures:
                return AgentResult(
                    task_id=request.task.id,
                    status=AgentResultStatus.FAILED,
                    summary_markdown="Task failed safely.",
                    error_code="source_failed",
                )
            return AgentResult(
                task_id=request.task.id,
                status=AgentResultStatus.SUCCEEDED,
                summary_markdown=f"Completed {request.task.id}.",
            )
        finally:
            self.active -= 1


class SlowExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request: StepExecutionRequest) -> AgentResult:
        self.calls += 1
        await asyncio.sleep(2)
        return AgentResult(
            task_id=request.task.id,
            status=AgentResultStatus.SUCCEEDED,
            summary_markdown="Too late.",
        )


def _database(tmp_path: Path) -> SqliteDatabase:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    return SqliteDatabase(paths, config.storage)


async def _insert_user(database: SqliteDatabase) -> None:
    async with database.create() as unit_of_work:
        timestamp = NOW.isoformat()
        await unit_of_work.connection.execute(
            "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
            "VALUES ('member-user-00001', 'Member', 'member', 'active', ?, ?)",
            (timestamp, timestamp),
        )
        await unit_of_work.commit()


async def _create_run(database: SqliteDatabase, index: int = 1) -> Run:
    service = RunService(
        SqliteRunStore(database),
        clock=lambda: NOW,
        id_factory=lambda: f"run-execution-{index:04d}",
    )
    creation = await service.create_run(
        RunRequest(
            request_id=f"request-execution-{index}",
            principal=Principal(
                "member-user-00001",
                UserRole.MEMBER,
                PrincipalChannel.DASHBOARD,
            ),
            text="Execute the approved orchestration Plan.",
            idempotency_key=f"execution-once-{index}",
            created_at=NOW,
        ),
        route_key="runs.create",
    )
    return creation.run


def _store(database: SqliteDatabase) -> SqliteOrchestrationExecutionStore:
    return SqliteOrchestrationExecutionStore(
        database,
        RedactionService(core_secret_redaction_policy()),
        SqliteRunEventWriter(core_telemetry_redaction_service()),
    )


def _queue(database: SqliteDatabase, clock: MutableClock) -> RunQueueService:
    return RunQueueService(
        SqliteRunQueueStore(database),
        RunQueuePolicy(2, timedelta(seconds=30), timedelta(seconds=10)),
        clock=clock,
    )


async def _claim(queue: RunQueueService, worker: str) -> RunClaim:
    claim = await queue.claim_next(worker_id=worker)
    assert claim is not None
    return claim


def _task(
    task_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    timeout_seconds: int = 60,
) -> DelegatedTask:
    return DelegatedTask(
        id=task_id,
        subagent="test-subagent",
        objective=f"Execute {task_id}.",
        depends_on=depends_on,
        timeout_seconds=timeout_seconds,
    )


def test_direct_plan_replay_conflict_and_secret_safe_completion(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            run = await _create_run(database)
            secret = "sk-private-direct-secret-123456789"
            plan = PreparedExecutionPlan(
                mode=RunMode.DIRECT,
                direct_answer=f"Answer containing {secret}",
            )
            clock = MutableClock(NOW + timedelta(seconds=1))
            executor = RecordingExecutor()
            engine = DependencyExecutionEngine(
                _store(database),
                executor,
                ExecutionPolicy(2),
                clock=clock,
            )

            first = await engine.materialize_and_enqueue(
                run_id=run.id,
                expected_revision=run.revision,
                plan=plan,
            )
            replay = await engine.materialize_and_enqueue(
                run_id=run.id,
                expected_revision=run.revision,
                plan=plan,
            )
            assert first.plan_fingerprint == replay.plan_fingerprint
            with pytest.raises(ExecutionPlanConflictError):
                await engine.materialize_and_enqueue(
                    run_id=run.id,
                    expected_revision=run.revision,
                    plan=PreparedExecutionPlan(
                        mode=RunMode.DIRECT,
                        direct_answer="Different answer.",
                    ),
                )

            clock.current += timedelta(seconds=1)
            claim = await _claim(_queue(database, clock), "worker-execution-0001")
            outcome = await engine.execute(claim)

            assert outcome.state is RunState.COMPOSING
            assert secret not in (outcome.direct_answer or "")
            assert executor.calls == []
            persisted = await SqliteRunStore(database).get_run(
                run_id=run.id,
                owner_user_id=None,
            )
            assert persisted is not None and persisted.state is RunState.COMPOSING
            async with database.create() as unit_of_work:
                plan_row = await fetch_one(
                    unit_of_work.connection,
                    "SELECT plan_json FROM run_execution_plans WHERE run_id = ?",
                    (run.id,),
                )
                await unit_of_work.commit()
            assert plan_row is not None
            assert secret not in str(plan_row["plan_json"])
        finally:
            await database.close()

    asyncio.run(scenario())


def test_delegate_respects_dependencies_parallel_limit_and_optional_failure(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            run = await _create_run(database)
            steps = (
                PreparedExecutionStep(_task("first")),
                PreparedExecutionStep(_task("second")),
                PreparedExecutionStep(_task("third", depends_on=("first",))),
                PreparedExecutionStep(
                    _task("optional", depends_on=("second",)),
                    requirement=StepRequirement.OPTIONAL,
                ),
            )
            plan = PreparedExecutionPlan(mode=RunMode.DELEGATE, steps=steps)
            clock = MutableClock(NOW + timedelta(seconds=1))
            executor = RecordingExecutor(failures=frozenset({"optional"}))
            engine = DependencyExecutionEngine(
                _store(database),
                executor,
                ExecutionPolicy(2),
                clock=clock,
            )
            await engine.materialize_and_enqueue(
                run_id=run.id,
                expected_revision=run.revision,
                plan=plan,
            )
            clock.current += timedelta(seconds=1)
            outcome = await engine.execute(
                await _claim(_queue(database, clock), "worker-execution-0001")
            )

            assert outcome.state is RunState.COMPOSING
            assert outcome.error_code is RunErrorCode.OPTIONAL_STEP_FAILED
            assert outcome.warnings == ("optional step failed: optional",)
            assert executor.peak == 2
            assert {request.task.id for request in executor.calls[:2]} == {"first", "second"}
            assert {request.task.id for request in executor.calls[2:]} == {"third", "optional"}
            dependencies = {request.task.id: request for request in executor.calls}
            assert tuple(result.task_id for result in dependencies["third"].dependency_results) == (
                "first",
            )
            assert tuple(
                result.task_id for result in dependencies["optional"].dependency_results
            ) == ("second",)

            persisted = await SqliteRunStore(database).get_run(
                run_id=run.id,
                owner_user_id=None,
            )
            assert persisted is not None
            assert persisted.state is RunState.COMPOSING
            assert persisted.error_code == RunErrorCode.OPTIONAL_STEP_FAILED.value
        finally:
            await database.close()

    asyncio.run(scenario())


def test_required_failure_prevents_dependent_execution(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            run = await _create_run(database)
            plan = PreparedExecutionPlan(
                mode=RunMode.DELEGATE,
                steps=(
                    PreparedExecutionStep(_task("required")),
                    PreparedExecutionStep(_task("dependent", depends_on=("required",))),
                ),
            )
            clock = MutableClock(NOW + timedelta(seconds=1))
            executor = RecordingExecutor(failures=frozenset({"required"}))
            engine = DependencyExecutionEngine(
                _store(database), executor, ExecutionPolicy(2), clock=clock
            )
            await engine.materialize_and_enqueue(
                run_id=run.id,
                expected_revision=run.revision,
                plan=plan,
            )
            clock.current += timedelta(seconds=1)
            outcome = await engine.execute(
                await _claim(_queue(database, clock), "worker-execution-0001")
            )

            assert outcome.state is RunState.FAILED
            assert outcome.error_code is RunErrorCode.REQUIRED_STEP_FAILED
            assert [request.task.id for request in executor.calls] == ["required"]
            async with database.create() as unit_of_work:
                rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT node_id, state FROM run_steps WHERE run_id = ? ORDER BY node_id",
                    (run.id,),
                )
                await unit_of_work.commit()
            assert {str(row["node_id"]): str(row["state"]) for row in rows} == {
                "dependent": "cancelled",
                "required": "failed",
            }
        finally:
            await database.close()

    asyncio.run(scenario())


def test_completed_result_is_reused_and_interrupted_idempotent_step_retries(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            run = await _create_run(database)
            plan = PreparedExecutionPlan(
                mode=RunMode.DELEGATE,
                steps=(
                    PreparedExecutionStep(_task("first"), idempotent=True),
                    PreparedExecutionStep(
                        _task("second", depends_on=("first",)),
                        idempotent=True,
                    ),
                ),
            )
            clock = MutableClock(NOW + timedelta(seconds=1))
            store = _store(database)
            bootstrap_engine = DependencyExecutionEngine(
                store,
                RecordingExecutor(),
                ExecutionPolicy(2),
                clock=clock,
            )
            await bootstrap_engine.materialize_and_enqueue(
                run_id=run.id,
                expected_revision=run.revision,
                plan=plan,
            )
            clock.current += timedelta(seconds=1)
            queue = _queue(database, clock)
            first_claim = await _claim(queue, "worker-execution-0001")
            snapshot = await store.load_for_claim(first_claim, at=clock.current)
            first_step = await store.start_step(
                claim=first_claim,
                step=snapshot.steps[0],
                at=clock.current,
            )
            result_secret = "sk-private-result-secret-123456789"
            await store.finish_step(
                claim=first_claim,
                step=first_step,
                state=StepState.COMPLETED,
                result=AgentResult(
                    task_id="first",
                    status=AgentResultStatus.SUCCEEDED,
                    summary_markdown=f"Persisted {result_secret}.",
                ),
                error_code=None,
                at=clock.current,
            )
            snapshot = await store.load_for_claim(first_claim, at=clock.current)
            await store.start_step(
                claim=first_claim,
                step=snapshot.steps[1],
                at=clock.current,
            )
            recovered = await queue.abandon_claim(first_claim, reason="handler_failed")
            assert recovered.requeued_run_ids == (run.id,)

            clock.current += timedelta(seconds=1)
            executor = RecordingExecutor()
            engine = DependencyExecutionEngine(
                store,
                executor,
                ExecutionPolicy(2),
                clock=clock,
            )
            outcome = await engine.execute(await _claim(queue, "worker-execution-0002"))

            assert outcome.state is RunState.COMPOSING
            assert [request.task.id for request in executor.calls] == ["second"]
            assert tuple(result.task_id for result in executor.calls[0].dependency_results) == (
                "first",
            )
            assert result_secret not in executor.calls[0].dependency_results[0].summary_markdown
            async with database.create() as unit_of_work:
                attempts = await fetch_all(
                    unit_of_work.connection,
                    "SELECT node_id, attempt, state FROM run_steps "
                    "WHERE run_id = ? ORDER BY node_id, attempt",
                    (run.id,),
                )
                persisted_results = await fetch_all(
                    unit_of_work.connection,
                    "SELECT result_json FROM run_steps WHERE run_id = ?",
                    (run.id,),
                )
                await unit_of_work.commit()
            assert [
                (str(row["node_id"]), int(row["attempt"]), str(row["state"])) for row in attempts
            ] == [
                ("first", 1, "completed"),
                ("second", 1, "interrupted"),
                ("second", 2, "completed"),
            ]
            assert result_secret not in " ".join(
                str(row["result_json"]) for row in persisted_results
            )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_cancelled_run_rejects_stale_worker_and_cancels_steps(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            run = await _create_run(database)
            plan = PreparedExecutionPlan(
                mode=RunMode.DELEGATE,
                steps=(PreparedExecutionStep(_task("first")),),
            )
            clock = MutableClock(NOW + timedelta(seconds=1))
            store = _store(database)
            engine = DependencyExecutionEngine(
                store,
                RecordingExecutor(),
                ExecutionPolicy(1),
                clock=clock,
            )
            await engine.materialize_and_enqueue(
                run_id=run.id,
                expected_revision=run.revision,
                plan=plan,
            )
            clock.current += timedelta(seconds=1)
            queue = _queue(database, clock)
            claim = await _claim(queue, "worker-execution-0001")
            snapshot = await store.load_for_claim(claim, at=clock.current)
            await queue.cancel(run_id=run.id)

            with pytest.raises(ExecutionOwnershipLostError):
                await store.start_step(
                    claim=claim,
                    step=snapshot.steps[0],
                    at=clock.current,
                )
            async with database.create() as unit_of_work:
                row = await fetch_one(
                    unit_of_work.connection,
                    "SELECT state, finished_at FROM run_steps WHERE run_id = ?",
                    (run.id,),
                )
                await unit_of_work.commit()
            assert row is not None
            assert str(row["state"]) == "cancelled"
            assert row["finished_at"] is not None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_task_timeout_fails_required_run_without_retry(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            run = await _create_run(database)
            plan = PreparedExecutionPlan(
                mode=RunMode.DELEGATE,
                steps=(PreparedExecutionStep(_task("slow", timeout_seconds=1)),),
            )
            clock = MutableClock(NOW + timedelta(seconds=1))
            executor = SlowExecutor()
            engine = DependencyExecutionEngine(
                _store(database), executor, ExecutionPolicy(1), clock=clock
            )
            await engine.materialize_and_enqueue(
                run_id=run.id,
                expected_revision=run.revision,
                plan=plan,
            )
            clock.current += timedelta(seconds=1)
            outcome = await engine.execute(
                await _claim(_queue(database, clock), "worker-execution-0001")
            )

            assert outcome.state is RunState.FAILED
            assert executor.calls == 1
            assert outcome.results[0].error_code == "step_timeout"
        finally:
            await database.close()

    asyncio.run(scenario())
