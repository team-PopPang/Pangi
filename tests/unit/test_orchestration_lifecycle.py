"""Orchestration submission and final Handler invariant tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from pangi.application.contracts.guardrails import (
    GuardedRunCreation,
    GuardrailDecision,
)
from pangi.application.contracts.orchestration import (
    OrchestratorDecision,
    ValidatedOrchestratorPlan,
)
from pangi.application.contracts.orchestration_execution import (
    ExecutionOutcome,
    ExecutionPlanSnapshot,
    PreparedExecutionPlan,
)
from pangi.application.contracts.orchestration_lifecycle import (
    OrchestrationDecisionRecord,
    OrchestrationFailureRecord,
    OrchestrationPlanningToken,
)
from pangi.application.contracts.root_orchestration import RootOrchestrationResult
from pangi.application.contracts.run_queue import RunClaim
from pangi.application.contracts.runs import RunCreation
from pangi.application.services.orchestration_lifecycle import (
    OrchestrationRunHandler,
    OrchestrationSubmissionService,
)
from pangi.application.services.plan_validator import (
    PlanValidationError,
    PlanValidationErrorCode,
)
from pangi.domain.auth import UserRole
from pangi.domain.guardrails import GuardrailOutcome, GuardrailStage, TrustLevel
from pangi.domain.model_routing import DataClass
from pangi.domain.runs import (
    Principal,
    PrincipalChannel,
    Run,
    RunMode,
    RunRequest,
    RunState,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class MemoryLifecycle:
    def __init__(self) -> None:
        self.started: list[tuple[str, int]] = []
        self.decisions: list[OrchestrationDecisionRecord] = []
        self.failures: list[OrchestrationFailureRecord] = []
        self.completed = 0
        self.composition_failures: list[str] = []

    async def start_planning(
        self,
        *,
        run_id: str,
        expected_revision: int,
        at: datetime,
    ) -> OrchestrationPlanningToken:
        self.started.append((run_id, expected_revision))
        return OrchestrationPlanningToken(run_id, expected_revision + 1)

    async def record_decision(
        self,
        *,
        token: OrchestrationPlanningToken,
        record: OrchestrationDecisionRecord,
        at: datetime,
    ) -> None:
        self.decisions.append(record)

    async def fail_planning(
        self,
        *,
        token: OrchestrationPlanningToken,
        failure: OrchestrationFailureRecord,
        at: datetime,
    ) -> None:
        self.failures.append(failure)

    async def complete_output(self, *, claim: RunClaim, output: object, at: datetime) -> None:
        self.completed += 1

    async def fail_composition(
        self,
        *,
        claim: RunClaim,
        error_code: str,
        at: datetime,
    ) -> None:
        self.composition_failures.append(error_code)


class RecordingRoot:
    def __init__(
        self,
        result: RootOrchestrationResult | None = None,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.result = result
        self.failure = failure
        self.calls = 0

    async def decide(self, request: object) -> RootOrchestrationResult:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        assert self.result is not None
        return self.result


class RecordingMaterializer:
    def __init__(self) -> None:
        self.plans: list[PreparedExecutionPlan] = []

    async def materialize_and_enqueue(
        self,
        *,
        run_id: str,
        expected_revision: int,
        plan: PreparedExecutionPlan,
    ) -> ExecutionPlanSnapshot:
        self.plans.append(plan)
        return ExecutionPlanSnapshot(plan, plan.fingerprint)


def _run(*, state: RunState = RunState.RECEIVED, revision: int = 0) -> Run:
    request = RunRequest(
        request_id="request-lifecycle-0001",
        principal=Principal(
            "principal-lifecycle-0001",
            UserRole.MEMBER,
            PrincipalChannel.API,
        ),
        text="Summarize this request.",
        idempotency_key="lifecycle-once-0001",
        created_at=NOW,
    )
    return Run(
        id="run-lifecycle-0001",
        request=request,
        state=state,
        updated_at=NOW,
        revision=revision,
    )


def _guarded_creation(
    *,
    state: RunState = RunState.RECEIVED,
    revision: int = 0,
    replayed: bool = False,
) -> GuardedRunCreation:
    return GuardedRunCreation(
        RunCreation(_run(state=state, revision=revision), replayed),
        GuardrailDecision(
            trust_level=TrustLevel.UNTRUSTED,
            stage=GuardrailStage.COMPLETE,
            outcome=GuardrailOutcome.ALLOWED,
            policy_version="input-v1",
            policy_fingerprint="a" * 64,
            unicode_policy_version="unicode-v1",
            text_bytes=23,
        ),
    )


def _root_result(mode: RunMode) -> RootOrchestrationResult:
    decision = OrchestratorDecision(
        mode=mode,
        direct_answer="A safe direct answer." if mode is RunMode.DIRECT else None,
        skill_name="weekly-summary" if mode is RunMode.SKILL else None,
    )
    return RootOrchestrationResult(
        ValidatedOrchestratorPlan(decision, (), 0),
        logical_call_count=0 if mode is RunMode.SKILL else 1,
        provider_request_count=0 if mode is RunMode.SKILL else 1,
    )


def test_direct_submission_records_one_decision_and_queues_one_plan() -> None:
    lifecycle = MemoryLifecycle()
    root = RecordingRoot(_root_result(RunMode.DIRECT))
    materializer = RecordingMaterializer()
    service = OrchestrationSubmissionService(
        lifecycle,
        root=root,
        materializer=materializer,
        clock=lambda: NOW,
    )

    result = asyncio.run(
        service.submit(
            _guarded_creation(),
            data_classes=frozenset({DataClass.INTERNAL}),
        )
    )

    assert result.state is RunState.QUEUED
    assert result.plan_fingerprint == materializer.plans[0].fingerprint
    assert root.calls == 1
    assert lifecycle.started == [("run-lifecycle-0001", 0)]
    assert len(lifecycle.decisions) == 1
    assert lifecycle.decisions[0].logical_call_count == 1
    assert lifecycle.failures == []


def test_invalid_decision_fails_before_any_plan_is_materialized() -> None:
    lifecycle = MemoryLifecycle()
    root = RecordingRoot(failure=PlanValidationError(PlanValidationErrorCode.UNKNOWN_SUBAGENT))
    materializer = RecordingMaterializer()
    service = OrchestrationSubmissionService(
        lifecycle,
        root=root,
        materializer=materializer,
        clock=lambda: NOW,
    )

    result = asyncio.run(
        service.submit(
            _guarded_creation(),
            data_classes=frozenset({DataClass.INTERNAL}),
        )
    )

    assert result.state is RunState.FAILED
    assert result.error_code == "unknown_subagent"
    assert root.calls == 1
    assert materializer.plans == []
    assert lifecycle.failures[0].logical_call_count == 1


def test_skill_and_interrupted_planning_never_materialize_or_repeat_root() -> None:
    lifecycle = MemoryLifecycle()
    root = RecordingRoot(_root_result(RunMode.SKILL))
    materializer = RecordingMaterializer()
    service = OrchestrationSubmissionService(
        lifecycle,
        root=root,
        materializer=materializer,
        clock=lambda: NOW,
    )

    skill = asyncio.run(
        service.submit(
            _guarded_creation(),
            data_classes=frozenset({DataClass.INTERNAL}),
        )
    )
    interrupted = asyncio.run(
        service.submit(
            _guarded_creation(
                state=RunState.PLANNING,
                revision=1,
                replayed=True,
            ),
            data_classes=frozenset({DataClass.INTERNAL}),
        )
    )

    assert skill.error_code == "skill_runtime_unavailable"
    assert lifecycle.decisions[0].logical_call_count == 0
    assert interrupted.error_code == "orchestration_planning_interrupted"
    assert root.calls == 1
    assert materializer.plans == []


class SnapshotStore:
    def __init__(self, plan: PreparedExecutionPlan) -> None:
        self.snapshot = ExecutionPlanSnapshot(plan, plan.fingerprint)

    async def load_for_claim(
        self,
        claim: RunClaim,
        *,
        at: datetime,
    ) -> ExecutionPlanSnapshot:
        return self.snapshot


class RecordingRunner:
    def __init__(self, outcome: ExecutionOutcome) -> None:
        self.outcome = outcome

    async def execute(self, claim: RunClaim) -> ExecutionOutcome:
        return self.outcome


class FailingComposer:
    def compose(self, plan: PreparedExecutionPlan, outcome: ExecutionOutcome) -> object:
        raise RuntimeError("candidate-secret-that-must-not-escape")


def test_handler_turns_unknown_composition_errors_into_a_stable_failure() -> None:
    plan = PreparedExecutionPlan(
        mode=RunMode.DIRECT,
        direct_answer="A safe direct answer.",
    )
    running = Run(
        id=_run().id,
        request=_run().request,
        state=RunState.RUNNING,
        updated_at=NOW,
        revision=2,
        worker_id="worker-lifecycle-0001",
        heartbeat_at=NOW,
        lease_expires_at=NOW + timedelta(seconds=30),
        started_at=NOW,
    )
    claim = RunClaim(running)
    lifecycle = MemoryLifecycle()
    handler = OrchestrationRunHandler(
        SnapshotStore(plan),  # type: ignore[arg-type]
        runner=RecordingRunner(
            ExecutionOutcome(
                run_id=running.id,
                state=RunState.COMPOSING,
                direct_answer=plan.direct_answer,
            )
        ),
        composer=FailingComposer(),  # type: ignore[arg-type]
        lifecycle=lifecycle,
        clock=lambda: NOW,
    )

    asyncio.run(handler.execute(claim))

    assert lifecycle.completed == 0
    assert lifecycle.composition_failures == ["output_guardrail_failed"]
