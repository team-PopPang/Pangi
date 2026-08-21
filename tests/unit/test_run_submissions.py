"""Trusted transport metadata and Queue handoff tests for local Run submission."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.guardrails import (
    GuardedRunCreation,
    GuardrailDecision,
)
from pangi.application.contracts.orchestration_lifecycle import OrchestrationSubmissionResult
from pangi.application.contracts.runs import RunCreation
from pangi.application.ports.run_queue import RunQueueUnavailableError
from pangi.application.services.run_submissions import LocalRunSubmissionService
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.guardrails import (
    GuardrailOutcome,
    GuardrailStage,
    TrustLevel,
)
from pangi.domain.model_routing import DataClass
from pangi.domain.runs import Run, RunRequest, RunState

NOW = datetime(2030, 1, 1, tzinfo=UTC)
RUN_ID = "run-identifier-0001"


def _decision() -> GuardrailDecision:
    return GuardrailDecision(
        trust_level=TrustLevel.UNTRUSTED,
        stage=GuardrailStage.COMPLETE,
        outcome=GuardrailOutcome.ALLOWED,
        policy_version="input-v1",
        policy_fingerprint="0" * 64,
        unicode_policy_version="unicode-v1",
        text_bytes=12,
    )


class GuardedRuns:
    def __init__(self) -> None:
        self.request: RunRequest | None = None
        self.route_key: str | None = None

    async def submit(self, *, request: RunRequest, route_key: str, **_kwargs: object):
        self.request = request
        self.route_key = route_key
        run = Run(
            id=RUN_ID,
            request=request,
            state=RunState.RECEIVED,
            updated_at=request.created_at,
        )
        return GuardedRunCreation(RunCreation(run, False), _decision())


class Orchestrator:
    def __init__(self, state: RunState) -> None:
        self.state = state
        self.data_classes: frozenset[DataClass] | None = None

    async def submit(
        self,
        creation: GuardedRunCreation,
        *,
        data_classes: frozenset[DataClass],
    ) -> OrchestrationSubmissionResult:
        self.data_classes = data_classes
        return OrchestrationSubmissionResult(
            creation.creation.run.id,
            self.state,
            replayed=creation.creation.replayed,
            error_code="model_policy_denied" if self.state is RunState.FAILED else None,
        )


class Runs:
    def __init__(self, guarded: GuardedRuns, state: RunState) -> None:
        self.guarded = guarded
        self.state = state

    async def get_run(self, **_kwargs: object) -> Run:
        assert self.guarded.request is not None
        run = Run(
            id=RUN_ID,
            request=self.guarded.request,
            state=RunState.RECEIVED,
            updated_at=NOW,
        )
        if self.state is RunState.FAILED:
            return replace(
                run,
                state=RunState.FAILED,
                revision=2,
                error_code="model_policy_denied",
                finished_at=NOW,
            )
        return replace(run, state=RunState.QUEUED, revision=2)


class Queue:
    def __init__(self, *, ready: bool = True) -> None:
        self.wake_count = 0
        self.ready = ready

    def wake(self) -> None:
        self.wake_count += 1

    def cancel_active(self, run_id: str) -> None:
        del run_id


def test_submission_owns_principal_transport_and_data_class_metadata() -> None:
    async def scenario() -> None:
        actor = AuthenticatedPrincipal(
            "member-user-00001",
            "Member",
            UserRole.MEMBER,
            UserStatus.ACTIVE,
        )
        guarded = GuardedRuns()
        orchestrator = Orchestrator(RunState.QUEUED)
        queue = Queue()
        service = LocalRunSubmissionService(
            guarded,  # type: ignore[arg-type]
            orchestrator=orchestrator,  # type: ignore[arg-type]
            runs=Runs(guarded, RunState.QUEUED),  # type: ignore[arg-type]
            queue=queue,
            data_classes=frozenset({DataClass.RESTRICTED}),
            clock=lambda: NOW,
            id_factory=lambda: "request-identifier-1",
        )

        result = await service.submit_run(
            actor=actor,
            text="local request",
            idempotency_key="submit-once",
            thread_key="thread-1",
            explicit_skill=None,
        )

        assert result.run.state is RunState.QUEUED
        assert guarded.route_key == "api.runs.create"
        assert guarded.request is not None
        assert guarded.request.principal.user_id == actor.user_id
        assert guarded.request.principal.role is actor.role
        assert guarded.request.principal.channel.value == "dashboard"
        assert guarded.request.request_id == "request-identifier-1"
        assert guarded.request.created_at == NOW
        assert orchestrator.data_classes == frozenset({DataClass.RESTRICTED})
        assert queue.wake_count == 1

    asyncio.run(scenario())


def test_failed_planning_does_not_wake_queue() -> None:
    async def scenario() -> None:
        actor = AuthenticatedPrincipal(
            "member-user-00001",
            "Member",
            UserRole.MEMBER,
            UserStatus.ACTIVE,
        )
        guarded = GuardedRuns()
        orchestrator = Orchestrator(RunState.FAILED)
        queue = Queue()
        service = LocalRunSubmissionService(
            guarded,  # type: ignore[arg-type]
            orchestrator=orchestrator,  # type: ignore[arg-type]
            runs=Runs(guarded, RunState.FAILED),  # type: ignore[arg-type]
            queue=queue,
            data_classes=frozenset({DataClass.RESTRICTED}),
            clock=lambda: NOW,
        )

        result = await service.submit_run(
            actor=actor,
            text="local request",
            idempotency_key="submit-once",
            thread_key=None,
            explicit_skill=None,
        )

        assert result.run.state is RunState.FAILED
        assert queue.wake_count == 0

    asyncio.run(scenario())


def test_unavailable_queue_rejects_before_guardrail_or_run_persistence() -> None:
    async def scenario() -> None:
        actor = AuthenticatedPrincipal(
            "member-user-00001",
            "Member",
            UserRole.MEMBER,
            UserStatus.ACTIVE,
        )
        guarded = GuardedRuns()
        queue = Queue(ready=False)
        service = LocalRunSubmissionService(
            guarded,  # type: ignore[arg-type]
            orchestrator=Orchestrator(RunState.QUEUED),  # type: ignore[arg-type]
            runs=Runs(guarded, RunState.QUEUED),  # type: ignore[arg-type]
            queue=queue,
            data_classes=frozenset({DataClass.RESTRICTED}),
            clock=lambda: NOW,
        )

        try:
            await service.submit_run(
                actor=actor,
                text="local request",
                idempotency_key="submit-once",
                thread_key=None,
                explicit_skill=None,
            )
        except RunQueueUnavailableError:
            pass
        else:  # pragma: no cover - assertion branch
            raise AssertionError("unavailable Queue must reject Run admission")

        assert guarded.request is None
        assert queue.wake_count == 0

    asyncio.run(scenario())
