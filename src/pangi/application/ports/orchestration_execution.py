"""Consumer-owned ports for durable dependency execution."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pangi.application.contracts.orchestration import AgentResult
from pangi.application.contracts.orchestration_execution import (
    ExecutionPlanSnapshot,
    ExecutionStepSnapshot,
    PreparedExecutionPlan,
    StepExecutionRequest,
)
from pangi.application.contracts.run_queue import RunClaim
from pangi.domain.runs import RunErrorCode, RunState, StepState


class OrchestrationExecutionError(RuntimeError):
    code = "orchestration_execution_failed"


class ExecutionPlanConflictError(OrchestrationExecutionError):
    code = "execution_plan_conflict"


class ExecutionPlanNotFoundError(OrchestrationExecutionError):
    code = "execution_plan_not_found"


class ExecutionOwnershipLostError(OrchestrationExecutionError):
    code = "execution_ownership_lost"


class ExecutionPersistenceError(OrchestrationExecutionError):
    code = "execution_persistence_error"


class OrchestrationExecutionStore(Protocol):
    async def materialize_and_enqueue(
        self,
        *,
        run_id: str,
        expected_revision: int,
        plan: PreparedExecutionPlan,
        at: datetime,
    ) -> ExecutionPlanSnapshot:
        """Persist one immutable Plan and atomically enqueue its Run."""

        ...

    async def load_for_claim(
        self,
        claim: RunClaim,
        *,
        at: datetime,
    ) -> ExecutionPlanSnapshot:
        """Load one Plan only while the supplied Worker still owns the Run."""

        ...

    async def retry_interrupted_step(
        self,
        *,
        claim: RunClaim,
        step: ExecutionStepSnapshot,
        at: datetime,
    ) -> ExecutionStepSnapshot:
        """Create the next attempt for an interrupted idempotent Step."""

        ...

    async def start_step(
        self,
        *,
        claim: RunClaim,
        step: ExecutionStepSnapshot,
        at: datetime,
    ) -> ExecutionStepSnapshot:
        """Move one owned queued Step to running."""

        ...

    async def finish_step(
        self,
        *,
        claim: RunClaim,
        step: ExecutionStepSnapshot,
        state: StepState,
        result: AgentResult,
        error_code: str | None,
        at: datetime,
    ) -> ExecutionStepSnapshot:
        """Persist one redacted result and terminal Step transition."""

        ...

    async def cancel_step(
        self,
        *,
        claim: RunClaim,
        step: ExecutionStepSnapshot,
        error_code: str,
        at: datetime,
    ) -> ExecutionStepSnapshot:
        """Cancel one queued Step whose dependency can no longer succeed."""

        ...

    async def finish_run(
        self,
        *,
        claim: RunClaim,
        state: RunState,
        warnings: tuple[str, ...],
        error_code: RunErrorCode | None,
        at: datetime,
    ) -> None:
        """Move the owned Run from running to composing or failed."""

        ...


class OrchestrationTaskExecutor(Protocol):
    async def execute(self, request: StepExecutionRequest) -> AgentResult:
        """Execute one prepared Task without calling another Subagent."""

        ...
