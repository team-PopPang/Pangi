"""Coordinate one Root decision, durable execution, and safe Output completion."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum

from pangi.application.contracts.guardrails import GuardedRunCreation, GuardedRunRequest
from pangi.application.contracts.model_routing import (
    ModelPolicyBlockedError,
    ModelProviderFailure,
)
from pangi.application.contracts.orchestration_execution import PreparedExecutionPlan
from pangi.application.contracts.orchestration_lifecycle import (
    OrchestrationDecisionRecord,
    OrchestrationFailureRecord,
    OrchestrationPlanningToken,
    OrchestrationSubmissionResult,
)
from pangi.application.contracts.root_orchestration import RootOrchestrationRequest
from pangi.application.contracts.run_queue import RunClaim
from pangi.application.ports.orchestration_execution import OrchestrationExecutionStore
from pangi.application.ports.orchestration_lifecycle import (
    ExecutionPlanMaterializer,
    ExecutionRunner,
    OrchestrationLifecycleError,
    OrchestrationLifecycleStore,
    RootDecisionMaker,
    SafeOutputComposer,
)
from pangi.domain.model_routing import DataClass
from pangi.domain.runs import RunMode, RunState

Clock = Callable[[], datetime]

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,119}$")
_ORCHESTRATION_FAILED = "orchestration_failed"
_PLANNING_INTERRUPTED = "orchestration_planning_interrupted"
_SKILL_RUNTIME_UNAVAILABLE = "skill_runtime_unavailable"
_OUTPUT_GUARDRAIL_FAILED = "output_guardrail_failed"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OrchestrationSubmissionService:
    """Plan each admitted Run once and hand only executable Plans to the queue."""

    def __init__(
        self,
        lifecycle: OrchestrationLifecycleStore,
        *,
        root: RootDecisionMaker,
        materializer: ExecutionPlanMaterializer,
        clock: Clock = _utc_now,
    ) -> None:
        self._lifecycle = lifecycle
        self._root = root
        self._materializer = materializer
        self._clock = clock

    async def submit(
        self,
        creation: GuardedRunCreation,
        *,
        data_classes: frozenset[DataClass],
    ) -> OrchestrationSubmissionResult:
        if not isinstance(creation, GuardedRunCreation):
            raise TypeError("creation must be GuardedRunCreation")
        normalized_classes = _data_classes(data_classes)
        current = creation.creation.run
        if current.state is RunState.PLANNING:
            token = OrchestrationPlanningToken(current.id, current.revision)
            failure = OrchestrationFailureRecord(_PLANNING_INTERRUPTED)
            await self._lifecycle.fail_planning(
                token=token,
                failure=failure,
                at=self._now(),
            )
            return OrchestrationSubmissionResult(
                current.id,
                RunState.FAILED,
                replayed=True,
                error_code=failure.error_code,
            )
        if current.state is not RunState.RECEIVED:
            return OrchestrationSubmissionResult(
                current.id,
                current.state,
                replayed=creation.creation.replayed,
                error_code=current.error_code,
            )

        token = await self._lifecycle.start_planning(
            run_id=current.id,
            expected_revision=current.revision,
            at=self._now(),
        )
        request = RootOrchestrationRequest(
            run_id=current.id,
            guarded_request=GuardedRunRequest(current.request, creation.decision),
            data_classes=normalized_classes,
        )
        try:
            decision = await self._root.decide(request)
        except Exception as error:
            failure = _root_failure(error, explicit_skill=current.request.explicit_skill)
            await self._fail_planning(token, failure)
            return OrchestrationSubmissionResult(
                current.id,
                RunState.FAILED,
                replayed=creation.creation.replayed,
                error_code=failure.error_code,
            )

        if decision.plan.decision.mode is RunMode.SKILL:
            record = OrchestrationDecisionRecord(
                mode=RunMode.SKILL,
                logical_call_count=decision.logical_call_count,
                provider_request_count=decision.provider_request_count,
                plan_fingerprint=None,
            )
            await self._lifecycle.record_decision(
                token=token,
                record=record,
                at=self._now(),
            )
            failure = OrchestrationFailureRecord(
                _SKILL_RUNTIME_UNAVAILABLE,
                logical_call_count=decision.logical_call_count,
                provider_request_count=decision.provider_request_count,
            )
            await self._fail_planning(token, failure)
            return OrchestrationSubmissionResult(
                current.id,
                RunState.FAILED,
                replayed=creation.creation.replayed,
                error_code=failure.error_code,
            )

        try:
            prepared = PreparedExecutionPlan.from_validated(decision.plan)
            record = OrchestrationDecisionRecord(
                mode=prepared.mode,
                logical_call_count=decision.logical_call_count,
                provider_request_count=decision.provider_request_count,
                plan_fingerprint=prepared.fingerprint,
            )
            await self._lifecycle.record_decision(
                token=token,
                record=record,
                at=self._now(),
            )
            snapshot = await self._materializer.materialize_and_enqueue(
                run_id=current.id,
                expected_revision=token.revision,
                plan=prepared,
            )
        except OrchestrationLifecycleError:
            raise
        except Exception as error:
            failure = OrchestrationFailureRecord(
                _stable_error_code(error),
                logical_call_count=decision.logical_call_count,
                provider_request_count=decision.provider_request_count,
            )
            await self._fail_planning(token, failure)
            return OrchestrationSubmissionResult(
                current.id,
                RunState.FAILED,
                replayed=creation.creation.replayed,
                error_code=failure.error_code,
            )
        return OrchestrationSubmissionResult(
            current.id,
            RunState.QUEUED,
            replayed=creation.creation.replayed,
            plan_fingerprint=snapshot.plan_fingerprint,
        )

    async def _fail_planning(
        self,
        token: OrchestrationPlanningToken,
        failure: OrchestrationFailureRecord,
    ) -> None:
        await self._lifecycle.fail_planning(
            token=token,
            failure=failure,
            at=self._now(),
        )

    def _now(self) -> datetime:
        return _aware_utc(self._clock(), field_name="orchestration clock")


class OrchestrationRunHandler:
    """Finish one claimed execution with a Guardrail-approved durable Output."""

    def __init__(
        self,
        executions: OrchestrationExecutionStore,
        *,
        runner: ExecutionRunner,
        composer: SafeOutputComposer,
        lifecycle: OrchestrationLifecycleStore,
        clock: Clock = _utc_now,
    ) -> None:
        self._executions = executions
        self._runner = runner
        self._composer = composer
        self._lifecycle = lifecycle
        self._clock = clock

    async def execute(self, claim: RunClaim) -> None:
        snapshot = await self._executions.load_for_claim(claim, at=self._now())
        outcome = await self._runner.execute(claim)
        if outcome.state is RunState.FAILED:
            return
        try:
            output = self._composer.compose(snapshot.plan, outcome)
        except Exception as error:
            await self._lifecycle.fail_composition(
                claim=claim,
                error_code=_composition_error_code(error),
                at=self._now(),
            )
            return
        await self._lifecycle.complete_output(
            claim=claim,
            output=output,
            at=self._now(),
        )

    def _now(self) -> datetime:
        return _aware_utc(self._clock(), field_name="orchestration handler clock")


def _data_classes(values: frozenset[DataClass]) -> frozenset[DataClass]:
    if not isinstance(values, frozenset) or not values:
        raise ValueError("data_classes must be a non-empty immutable frozenset")
    try:
        return frozenset(DataClass(value) for value in values)
    except ValueError as error:
        raise ValueError("data_classes contains an invalid value") from error


def _root_failure(error: Exception, *, explicit_skill: str | None) -> OrchestrationFailureRecord:
    error_code = _stable_error_code(error)
    logical_calls = (
        0 if explicit_skill is not None or error_code == "root_catalog_unavailable" else 1
    )
    provider_requests: int | None = None
    if isinstance(error, ModelProviderFailure):
        provider_requests = error.provider_request_count
    elif isinstance(error, ModelPolicyBlockedError):
        provider_requests = 0
    return OrchestrationFailureRecord(
        error_code,
        logical_call_count=logical_calls,
        provider_request_count=provider_requests,
    )


def _composition_error_code(error: Exception) -> str:
    value = _stable_error_code(error)
    return value if value != _ORCHESTRATION_FAILED else _OUTPUT_GUARDRAIL_FAILED


def _stable_error_code(error: Exception) -> str:
    value: object = getattr(error, "code", None)
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str) and _ERROR_CODE.fullmatch(value) is not None:
        return value
    return _ORCHESTRATION_FAILED


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must return a timezone-aware datetime")
    return value.astimezone(UTC)
