"""SQLite orchestration lifecycle, Output, and composition lease integration tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all, fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.event_writer import SqliteRunEventWriter
from pangi.adapters.outbound.persistence.sqlite.orchestration_execution import (
    SqliteOrchestrationExecutionStore,
)
from pangi.adapters.outbound.persistence.sqlite.orchestration_lifecycle import (
    SqliteOrchestrationLifecycleStore,
)
from pangi.adapters.outbound.persistence.sqlite.runs import (
    SqliteRunQueueStore,
    SqliteRunStore,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.guardrails import (
    GuardedRunCreation,
    GuardrailDecision,
)
from pangi.application.contracts.orchestration import (
    AgentResult,
    AgentResultStatus,
    DelegatedTask,
    OrchestratorDecision,
    ValidatedOrchestratorPlan,
)
from pangi.application.contracts.orchestration_execution import (
    ExecutionPolicy,
    StepExecutionRequest,
)
from pangi.application.contracts.output_guardrails import OutputGuardrailPolicy
from pangi.application.contracts.root_orchestration import RootOrchestrationResult
from pangi.application.contracts.run_queue import RunQueuePolicy
from pangi.application.services.execution_engine import DependencyExecutionEngine
from pangi.application.services.orchestration_lifecycle import (
    OrchestrationRunHandler,
    OrchestrationSubmissionService,
)
from pangi.application.services.output_guardrails import (
    OutputGuardrailService,
    core_output_internal_detail_rules,
)
from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)
from pangi.application.services.result_reducer import OrchestrationOutputComposer
from pangi.application.services.run_queue import RunQueueService
from pangi.application.services.runs import RunService
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)
from pangi.domain.auth import UserRole
from pangi.domain.guardrails import GuardrailOutcome, GuardrailStage, TrustLevel
from pangi.domain.model_routing import DataClass
from pangi.domain.runs import Principal, PrincipalChannel, RunMode, RunRequest, RunState

NOW = datetime(2030, 1, 1, tzinfo=UTC)
SECRET = "sk-orchestration-output-secret-123456789"


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class DirectRoot:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls = 0

    async def decide(self, request: object) -> RootOrchestrationResult:
        self.calls += 1
        decision = OrchestratorDecision(
            mode=RunMode.DIRECT,
            direct_answer=self.answer,
        )
        return RootOrchestrationResult(
            ValidatedOrchestratorPlan(decision, (), 0),
            logical_call_count=1,
            provider_request_count=1,
        )


class DelegateRoot:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, request: object) -> RootOrchestrationResult:
        self.calls += 1
        task = DelegatedTask(
            id="collect-summary",
            subagent="test-subagent",
            objective="Collect one safe summary.",
            timeout_seconds=60,
        )
        decision = OrchestratorDecision(
            mode=RunMode.DELEGATE,
            tasks=(task,),
        )
        return RootOrchestrationResult(
            ValidatedOrchestratorPlan(decision, (task,), 60),
            logical_call_count=1,
            provider_request_count=1,
        )


class NoSubagentExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request: StepExecutionRequest) -> AgentResult:
        self.calls += 1
        raise AssertionError("Direct execution must not call a Subagent")


class OneSubagentExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request: StepExecutionRequest) -> AgentResult:
        self.calls += 1
        return AgentResult(
            task_id=request.task.id,
            status=AgentResultStatus.SUCCEEDED,
            summary_markdown="Delegated summary completed.",
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


async def _guarded_creation(database: SqliteDatabase) -> GuardedRunCreation:
    service = RunService(
        SqliteRunStore(database),
        clock=lambda: NOW,
        id_factory=lambda: "run-lifecycle-0001",
    )
    creation = await service.create_run(
        RunRequest(
            request_id="request-lifecycle-0001",
            principal=Principal(
                "member-user-00001",
                UserRole.MEMBER,
                PrincipalChannel.API,
            ),
            text="Return a direct response.",
            idempotency_key="lifecycle-once-0001",
            created_at=NOW,
        ),
        route_key="runs.create",
    )
    return GuardedRunCreation(
        creation,
        GuardrailDecision(
            trust_level=TrustLevel.UNTRUSTED,
            stage=GuardrailStage.COMPLETE,
            outcome=GuardrailOutcome.ALLOWED,
            policy_version="input-v1",
            policy_fingerprint="a" * 64,
            unicode_policy_version="unicode-v1",
            text_bytes=25,
        ),
    )


def _execution_store(database: SqliteDatabase) -> SqliteOrchestrationExecutionStore:
    return SqliteOrchestrationExecutionStore(
        database,
        RedactionService(core_secret_redaction_policy()),
        SqliteRunEventWriter(core_telemetry_redaction_service()),
    )


def _lifecycle_store(database: SqliteDatabase) -> SqliteOrchestrationLifecycleStore:
    return SqliteOrchestrationLifecycleStore(
        database,
        SqliteRunEventWriter(core_telemetry_redaction_service()),
    )


def _composer() -> OrchestrationOutputComposer:
    guardrail = OutputGuardrailService(
        OutputGuardrailPolicy(
            policy_version="orchestration-output-v1",
            max_input_bytes=100_000,
            max_output_bytes=50_000,
            max_mentions=2,
            max_evidence_links=20,
            max_evidence_link_bytes=2_048,
            allowed_link_schemes=frozenset({"https"}),
            allow_relative_links=False,
            broadcast_mentions=frozenset({"@channel", "@everyone", "@here"}),
            internal_detail_rules=core_output_internal_detail_rules(),
            truncation_marker="\n\n[OUTPUT TRUNCATED]",
        ),
        redactor=RedactionService(core_secret_redaction_policy()),
    )
    return OrchestrationOutputComposer(guardrail)


def test_direct_run_is_planned_executed_sanitized_and_completed(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            creation = await _guarded_creation(database)
            clock = MutableClock(NOW + timedelta(seconds=1))
            execution_store = _execution_store(database)
            lifecycle_store = _lifecycle_store(database)
            executor = NoSubagentExecutor()
            engine = DependencyExecutionEngine(
                execution_store,
                executor,
                ExecutionPolicy(2),
                clock=clock,
            )
            root = DirectRoot(f"<b>Safe summary</b> with {SECRET}")
            submission = OrchestrationSubmissionService(
                lifecycle_store,
                root=root,
                materializer=engine,
                clock=clock,
            )

            planned = await submission.submit(
                creation,
                data_classes=frozenset({DataClass.INTERNAL}),
            )
            assert planned.state is RunState.QUEUED
            clock.current += timedelta(seconds=1)
            queue = RunQueueService(
                SqliteRunQueueStore(database),
                RunQueuePolicy(1, timedelta(seconds=30), timedelta(seconds=10)),
                clock=clock,
            )
            claim = await queue.claim_next(worker_id="worker-lifecycle-0001")
            assert claim is not None
            handler = OrchestrationRunHandler(
                execution_store,
                runner=engine,
                composer=_composer(),
                lifecycle=lifecycle_store,
                clock=clock,
            )
            await handler.execute(claim)

            persisted = await SqliteRunStore(database).get_run(
                run_id=creation.creation.run.id,
                owner_user_id=None,
            )
            assert persisted is not None
            assert persisted.state is RunState.COMPLETED
            assert persisted.worker_id is None
            assert root.calls == 1
            assert executor.calls == 0
            async with database.create() as unit_of_work:
                output = await fetch_one(
                    unit_of_work.connection,
                    "SELECT markdown, content_fingerprint FROM run_outputs WHERE run_id = ?",
                    (creation.creation.run.id,),
                )
                events = await fetch_all(
                    unit_of_work.connection,
                    "SELECT type, attributes_json FROM run_events WHERE run_id = ? "
                    "ORDER BY event_index",
                    (creation.creation.run.id,),
                )
                await unit_of_work.commit()
            assert output is not None
            assert SECRET not in str(output["markdown"])
            serialized = "\n".join(str(row["attributes_json"]) for row in events)
            assert SECRET not in serialized
            assert [str(row["type"]) for row in events] == [
                "run.received",
                "run.planning",
                "orchestrator.started",
                "orchestrator.decided",
                "run.queued",
                "run.running",
                "run.composing",
                "output.redacted",
                "output.completed",
                "run.completed",
            ]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_composing_lease_renews_and_abandonment_fails_without_output(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            creation = await _guarded_creation(database)
            clock = MutableClock(NOW + timedelta(seconds=1))
            execution_store = _execution_store(database)
            lifecycle_store = _lifecycle_store(database)
            engine = DependencyExecutionEngine(
                execution_store,
                NoSubagentExecutor(),
                ExecutionPolicy(1),
                clock=clock,
            )
            await OrchestrationSubmissionService(
                lifecycle_store,
                root=DirectRoot("Safe direct response."),
                materializer=engine,
                clock=clock,
            ).submit(
                creation,
                data_classes=frozenset({DataClass.INTERNAL}),
            )
            queue = RunQueueService(
                SqliteRunQueueStore(database),
                RunQueuePolicy(1, timedelta(seconds=30), timedelta(seconds=10)),
                clock=clock,
            )
            claim = await queue.claim_next(worker_id="worker-lifecycle-0001")
            assert claim is not None
            outcome = await engine.execute(claim)
            assert outcome.state is RunState.COMPOSING

            clock.current += timedelta(seconds=5)
            assert await queue.heartbeat(claim)
            recovery = await queue.abandon_claim(claim, reason="handler_returned")
            assert recovery.failed_run_ids == (creation.creation.run.id,)
            persisted = await SqliteRunStore(database).get_run(
                run_id=creation.creation.run.id,
                owner_user_id=None,
            )
            assert persisted is not None
            assert persisted.state is RunState.FAILED
            assert persisted.error_code == "composition_interrupted"
            async with database.create() as unit_of_work:
                output = await fetch_one(
                    unit_of_work.connection,
                    "SELECT run_id FROM run_outputs WHERE run_id = ?",
                    (creation.creation.run.id,),
                )
                await unit_of_work.commit()
            assert output is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_delegate_run_executes_only_the_prepared_task_and_completes(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            creation = await _guarded_creation(database)
            clock = MutableClock(NOW + timedelta(seconds=1))
            execution_store = _execution_store(database)
            lifecycle_store = _lifecycle_store(database)
            executor = OneSubagentExecutor()
            engine = DependencyExecutionEngine(
                execution_store,
                executor,
                ExecutionPolicy(1),
                clock=clock,
            )
            root = DelegateRoot()
            result = await OrchestrationSubmissionService(
                lifecycle_store,
                root=root,
                materializer=engine,
                clock=clock,
            ).submit(
                creation,
                data_classes=frozenset({DataClass.INTERNAL}),
            )
            assert result.state is RunState.QUEUED

            queue = RunQueueService(
                SqliteRunQueueStore(database),
                RunQueuePolicy(1, timedelta(seconds=30), timedelta(seconds=10)),
                clock=clock,
            )
            claim = await queue.claim_next(worker_id="worker-lifecycle-0001")
            assert claim is not None
            await OrchestrationRunHandler(
                execution_store,
                runner=engine,
                composer=_composer(),
                lifecycle=lifecycle_store,
                clock=clock,
            ).execute(claim)

            persisted = await SqliteRunStore(database).get_run(
                run_id=creation.creation.run.id,
                owner_user_id=None,
            )
            assert persisted is not None and persisted.state is RunState.COMPLETED
            assert root.calls == 1
            assert executor.calls == 1
            async with database.create() as unit_of_work:
                output = await fetch_one(
                    unit_of_work.connection,
                    "SELECT markdown FROM run_outputs WHERE run_id = ?",
                    (creation.creation.run.id,),
                )
                step_count = await fetch_one(
                    unit_of_work.connection,
                    "SELECT COUNT(*) AS value FROM run_steps WHERE run_id = ?",
                    (creation.creation.run.id,),
                )
                await unit_of_work.commit()
            assert output is not None
            assert "Delegated summary completed." in str(output["markdown"])
            assert step_count is not None and int(step_count["value"]) == 1
        finally:
            await database.close()

    asyncio.run(scenario())
