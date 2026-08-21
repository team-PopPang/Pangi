"""Guarded local submission through the durable Queue Runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.input_rate_limits import InMemoryInputRateLimiter
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all
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
from pangi.adapters.outbound.skill_authorization import UnavailableExplicitSkillAuthorizer
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.guardrails import InputGuardrailPolicy
from pangi.application.contracts.orchestration import (
    OrchestratorDecision,
    ValidatedOrchestratorPlan,
)
from pangi.application.contracts.orchestration_execution import (
    ExecutionPolicy,
    StepExecutionRequest,
)
from pangi.application.contracts.output_guardrails import OutputGuardrailPolicy
from pangi.application.contracts.root_orchestration import (
    RootOrchestrationRequest,
    RootOrchestrationResult,
)
from pangi.application.contracts.run_queue import RunQueuePolicy
from pangi.application.services.execution_engine import DependencyExecutionEngine
from pangi.application.services.input_guardrails import (
    GuardedRunSubmissionService,
    InputGuardrailService,
)
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
from pangi.application.services.run_queue import RunQueueRuntime, RunQueueService
from pangi.application.services.run_submissions import LocalRunSubmissionService
from pangi.application.services.runs import RunService
from pangi.application.services.runtime_lifecycle import CompositeRuntimeBackend
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.model_routing import DataClass
from pangi.domain.runs import RunMode, RunState


class DirectRoot:
    def __init__(self) -> None:
        self.calls = 0
        self.data_classes: frozenset[DataClass] | None = None

    async def decide(self, request: RootOrchestrationRequest) -> RootOrchestrationResult:
        self.calls += 1
        self.data_classes = request.data_classes
        decision = OrchestratorDecision(
            mode=RunMode.DIRECT,
            direct_answer="Direct runtime answer.",
        )
        return RootOrchestrationResult(
            ValidatedOrchestratorPlan(decision, (), 0),
            logical_call_count=1,
            provider_request_count=1,
        )


class NoSubagentExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request: StepExecutionRequest):
        self.calls += 1
        raise AssertionError(f"Direct Run called a Subagent for {request.run_id}")


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


async def _insert_user(database: SqliteDatabase, now: datetime) -> None:
    async with database.create() as unit_of_work:
        timestamp = now.isoformat()
        await unit_of_work.connection.execute(
            "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
            "VALUES ('member-user-00001', 'Member', 'member', 'active', ?, ?)",
            (timestamp, timestamp),
        )
        await unit_of_work.commit()


def _input_policy() -> InputGuardrailPolicy:
    return InputGuardrailPolicy(
        policy_version="local-input-v1",
        unicode_policy_version="unicode-v1",
        max_text_bytes=100_000,
        max_attachment_count=0,
        max_attachment_bytes=0,
        max_total_attachment_bytes=0,
        allowed_media_types=frozenset(),
        prohibited_codepoints=frozenset({0x200B, 0x202E}),
        rate_limit=60,
        rate_window_seconds=60,
    )


def _composer() -> OrchestrationOutputComposer:
    policy = OutputGuardrailPolicy(
        policy_version="local-output-v1",
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
    )
    return OrchestrationOutputComposer(
        OutputGuardrailService(
            policy,
            redactor=RedactionService(core_secret_redaction_policy()),
        )
    )


async def _wait_for_completion(
    runs: RunService,
    actor: AuthenticatedPrincipal,
    run_id: str,
) -> None:
    for _attempt in range(500):
        run = await runs.get_run(actor=actor, run_id=run_id)
        if run.state is RunState.COMPLETED:
            return
        await asyncio.sleep(0)
    raise AssertionError("Run did not complete through the Queue Runtime")


def test_guarded_submission_wakes_queue_and_completes_direct_run_once(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = datetime.now(UTC)
        database = _database(tmp_path)
        await database.start()
        await _insert_user(database, now)
        await database.close()

        event_writer = SqliteRunEventWriter(core_telemetry_redaction_service())
        executions = SqliteOrchestrationExecutionStore(
            database,
            RedactionService(core_secret_redaction_policy()),
            event_writer,
        )
        lifecycle = SqliteOrchestrationLifecycleStore(database, event_writer)
        executor = NoSubagentExecutor()
        engine = DependencyExecutionEngine(executions, executor, ExecutionPolicy(1))
        root = DirectRoot()
        handler = OrchestrationRunHandler(
            executions,
            runner=engine,
            composer=_composer(),
            lifecycle=lifecycle,
        )
        queue = RunQueueRuntime(
            RunQueueService(
                SqliteRunQueueStore(database, event_writer),
                RunQueuePolicy(1, timedelta(seconds=30), timedelta(seconds=10)),
            ),
            handler,
            worker_id_factory=lambda: "worker-submission-0001",
        )
        runs = RunService(SqliteRunStore(database, event_writer))
        guarded = GuardedRunSubmissionService(
            InputGuardrailService(
                _input_policy(),
                skill_authorizer=UnavailableExplicitSkillAuthorizer(),
                rate_limiter=InMemoryInputRateLimiter(max_keys=100),
                clock=lambda: now,
            ),
            run_creator=runs,
        )
        submissions = LocalRunSubmissionService(
            guarded,
            orchestrator=OrchestrationSubmissionService(
                lifecycle,
                root=root,
                materializer=engine,
            ),
            runs=runs,
            queue=queue,
            data_classes=frozenset({DataClass.RESTRICTED}),
            clock=lambda: now,
            id_factory=iter(("request-submission-0001", "request-submission-0002")).__next__,
        )
        actor = AuthenticatedPrincipal(
            "member-user-00001",
            "Member",
            UserRole.MEMBER,
            UserStatus.ACTIVE,
        )
        runtime = CompositeRuntimeBackend((database, queue))

        await runtime.start()
        try:
            created = await submissions.submit_run(
                actor=actor,
                text="Return one direct answer.",
                idempotency_key="runtime-submit-once",
                thread_key=None,
                explicit_skill=None,
            )
            await _wait_for_completion(runs, actor, created.run.id)
            replayed = await submissions.submit_run(
                actor=actor,
                text="Return one direct answer.",
                idempotency_key="runtime-submit-once",
                thread_key=None,
                explicit_skill=None,
            )

            assert replayed.replayed
            assert replayed.run.id == created.run.id
            assert replayed.run.state is RunState.COMPLETED
            assert root.calls == 1
            assert root.data_classes == frozenset({DataClass.RESTRICTED})
            assert executor.calls == 0
            async with database.create() as unit_of_work:
                rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT type FROM run_events WHERE run_id = ? ORDER BY event_index",
                    (created.run.id,),
                )
                output_rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT markdown FROM run_outputs WHERE run_id = ?",
                    (created.run.id,),
                )
            event_types = [str(row["type"]) for row in rows]
            assert event_types.index("run.received") < event_types.index("run.planning")
            assert event_types.index("run.queued") < event_types.index("run.running")
            assert event_types.index("run.running") < event_types.index("run.completed")
            assert [str(row["markdown"]) for row in output_rows] == [
                "Direct runtime answer."
            ]
        finally:
            await runtime.close()

    asyncio.run(scenario())
