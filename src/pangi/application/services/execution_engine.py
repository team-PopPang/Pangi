"""Deterministic, bounded execution of one persisted orchestration Plan."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from pangi.application.contracts.orchestration import AgentResult, AgentResultStatus
from pangi.application.contracts.orchestration_execution import (
    ExecutionOutcome,
    ExecutionPlanSnapshot,
    ExecutionPolicy,
    ExecutionStepSnapshot,
    PreparedExecutionPlan,
    StepExecutionRequest,
)
from pangi.application.contracts.run_queue import RunClaim
from pangi.application.ports.orchestration_execution import (
    ExecutionPersistenceError,
    OrchestrationExecutionStore,
    OrchestrationTaskExecutor,
)
from pangi.domain.runs import (
    RunErrorCode,
    RunMode,
    RunState,
    StepRequirement,
    StepState,
)

Clock = Callable[[], datetime]

_STEP_TIMEOUT = "step_timeout"
_STEP_EXECUTION_FAILED = "step_execution_failed"
_STEP_RESULT_INVALID = "step_result_invalid"
_DEPENDENCY_FAILED = "dependency_failed"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DependencyExecutionEngine:
    def __init__(
        self,
        store: OrchestrationExecutionStore,
        executor: OrchestrationTaskExecutor,
        policy: ExecutionPolicy,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._store = store
        self._executor = executor
        self._policy = policy
        self._clock = clock

    async def materialize_and_enqueue(
        self,
        *,
        run_id: str,
        expected_revision: int,
        plan: PreparedExecutionPlan,
    ) -> ExecutionPlanSnapshot:
        return await self._store.materialize_and_enqueue(
            run_id=run_id,
            expected_revision=expected_revision,
            plan=plan,
            at=self._now(),
        )

    async def execute(self, claim: RunClaim) -> ExecutionOutcome:
        snapshot = await self._store.load_for_claim(claim, at=self._now())
        if snapshot.plan.mode is RunMode.DIRECT:
            await self._store.finish_run(
                claim=claim,
                state=RunState.COMPOSING,
                warnings=(),
                error_code=None,
                at=self._now(),
            )
            return ExecutionOutcome(
                run_id=claim.run_id,
                state=RunState.COMPOSING,
                direct_answer=snapshot.plan.direct_answer,
            )

        snapshot = await self._prepare_recovery(claim, snapshot)
        while True:
            required_failed = _required_failure(snapshot)
            if required_failed:
                snapshot = await self._cancel_remaining(claim, snapshot)
                return await self._finish_delegate(claim, snapshot)

            blocked = _dependency_blocked_steps(snapshot)
            if blocked:
                for step in blocked:
                    await self._store.cancel_step(
                        claim=claim,
                        step=step,
                        error_code=_DEPENDENCY_FAILED,
                        at=self._now(),
                    )
                snapshot = await self._store.load_for_claim(claim, at=self._now())
                continue

            queued = tuple(
                step
                for step in snapshot.steps
                if step.step.state is StepState.QUEUED and _dependencies_completed(step, snapshot)
            )
            if queued:
                batch = queued[: self._policy.max_parallel_steps]
                started = tuple(
                    [
                        await self._store.start_step(
                            claim=claim,
                            step=step,
                            at=self._now(),
                        )
                        for step in batch
                    ]
                )
                await asyncio.gather(
                    *(self._execute_one(claim, step, snapshot) for step in started)
                )
                snapshot = await self._store.load_for_claim(claim, at=self._now())
                continue

            if all(_terminal(step) for step in snapshot.steps):
                return await self._finish_delegate(claim, snapshot)
            raise ExecutionPersistenceError("Persisted dependency execution is stuck")

    async def _prepare_recovery(
        self,
        claim: RunClaim,
        snapshot: ExecutionPlanSnapshot,
    ) -> ExecutionPlanSnapshot:
        for step in snapshot.steps:
            if step.step.state is not StepState.INTERRUPTED:
                continue
            if not step.step.idempotent:
                raise ExecutionPersistenceError(
                    "A non-idempotent interrupted Step reached execution"
                )
            await self._store.retry_interrupted_step(
                claim=claim,
                step=step,
                at=self._now(),
            )
        return await self._store.load_for_claim(claim, at=self._now())

    async def _execute_one(
        self,
        claim: RunClaim,
        step: ExecutionStepSnapshot,
        snapshot: ExecutionPlanSnapshot,
    ) -> None:
        dependencies = _dependency_results(step, snapshot)
        request = StepExecutionRequest(
            run_id=claim.run_id,
            step_id=step.step.id,
            task=step.definition.task,
            dependency_results=dependencies,
        )
        try:
            result = await asyncio.wait_for(
                self._executor.execute(request),
                timeout=step.definition.task.timeout_seconds,
            )
        except TimeoutError:
            result = _failed_result(step.definition.task.id, _STEP_TIMEOUT)
        except Exception:
            result = _failed_result(step.definition.task.id, _STEP_EXECUTION_FAILED)
        if not isinstance(result, AgentResult) or result.task_id != step.definition.task.id:
            result = _failed_result(step.definition.task.id, _STEP_RESULT_INVALID)
        state = (
            StepState.FAILED if result.status is AgentResultStatus.FAILED else StepState.COMPLETED
        )
        await self._store.finish_step(
            claim=claim,
            step=step,
            state=state,
            result=result,
            error_code=result.error_code if state is StepState.FAILED else None,
            at=self._now(),
        )

    async def _cancel_remaining(
        self,
        claim: RunClaim,
        snapshot: ExecutionPlanSnapshot,
    ) -> ExecutionPlanSnapshot:
        for step in snapshot.steps:
            if step.step.state is StepState.QUEUED:
                await self._store.cancel_step(
                    claim=claim,
                    step=step,
                    error_code=_DEPENDENCY_FAILED,
                    at=self._now(),
                )
        return await self._store.load_for_claim(claim, at=self._now())

    async def _finish_delegate(
        self,
        claim: RunClaim,
        snapshot: ExecutionPlanSnapshot,
    ) -> ExecutionOutcome:
        failures = tuple(
            step
            for step in snapshot.steps
            if step.step.state in {StepState.FAILED, StepState.CANCELLED}
        )
        required_failed = any(
            step.step.requirement is StepRequirement.REQUIRED for step in failures
        )
        results = tuple(step.result for step in snapshot.steps if step.result is not None)
        error_code: RunErrorCode | None
        if required_failed:
            state = RunState.FAILED
            warnings: tuple[str, ...] = ()
            error_code = RunErrorCode.REQUIRED_STEP_FAILED
        else:
            state = RunState.COMPOSING
            optional_warnings = tuple(
                f"optional step failed: {step.step.node_id}" for step in failures
            )
            partial_warnings = tuple(
                warning
                for result in results
                for warning in (
                    ((f"partial result: {result.task_id}",) + result.warnings)
                    if result.status is AgentResultStatus.PARTIAL
                    else result.warnings
                )
            )
            warnings = optional_warnings + partial_warnings
            error_code = RunErrorCode.OPTIONAL_STEP_FAILED if optional_warnings else None
        await self._store.finish_run(
            claim=claim,
            state=state,
            warnings=warnings,
            error_code=error_code,
            at=self._now(),
        )
        return ExecutionOutcome(
            run_id=claim.run_id,
            state=state,
            results=results,
            warnings=warnings,
            error_code=error_code,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("execution clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _dependency_results(
    step: ExecutionStepSnapshot,
    snapshot: ExecutionPlanSnapshot,
) -> tuple[AgentResult, ...]:
    by_node = {item.step.node_id: item for item in snapshot.steps}
    results: list[AgentResult] = []
    for dependency in step.definition.task.depends_on:
        result = by_node[dependency].result
        if result is None:
            raise ExecutionPersistenceError("A completed dependency Result is unavailable")
        results.append(result)
    return tuple(results)


def _dependencies_completed(
    step: ExecutionStepSnapshot,
    snapshot: ExecutionPlanSnapshot,
) -> bool:
    states = {item.step.node_id: item.step.state for item in snapshot.steps}
    return all(states[dependency] is StepState.COMPLETED for dependency in step.step.depends_on)


def _dependency_blocked_steps(
    snapshot: ExecutionPlanSnapshot,
) -> tuple[ExecutionStepSnapshot, ...]:
    states = {item.step.node_id: item.step.state for item in snapshot.steps}
    failed = {StepState.FAILED, StepState.CANCELLED}
    return tuple(
        step
        for step in snapshot.steps
        if step.step.state is StepState.QUEUED
        and any(states[dependency] in failed for dependency in step.step.depends_on)
    )


def _required_failure(snapshot: ExecutionPlanSnapshot) -> bool:
    return any(
        step.step.requirement is StepRequirement.REQUIRED
        and step.step.state in {StepState.FAILED, StepState.CANCELLED}
        for step in snapshot.steps
    )


def _terminal(step: ExecutionStepSnapshot) -> bool:
    return step.step.state in {
        StepState.COMPLETED,
        StepState.FAILED,
        StepState.CANCELLED,
    }


def _failed_result(task_id: str, error_code: str) -> AgentResult:
    return AgentResult(
        task_id=task_id,
        status=AgentResultStatus.FAILED,
        summary_markdown="Task execution failed.",
        error_code=error_code,
    )
