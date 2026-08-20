"""Consumer-owned ports for orchestration planning and final Output storage."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

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
from pangi.application.contracts.output_guardrails import SafeOutput
from pangi.application.contracts.root_orchestration import (
    RootOrchestrationRequest,
    RootOrchestrationResult,
)
from pangi.application.contracts.run_queue import RunClaim


class OrchestrationLifecycleError(RuntimeError):
    code = "orchestration_lifecycle_failed"


class OrchestrationLifecycleConflictError(OrchestrationLifecycleError):
    code = "orchestration_lifecycle_conflict"


class OrchestrationLifecycleNotFoundError(OrchestrationLifecycleError):
    code = "orchestration_lifecycle_not_found"


class OrchestrationLifecyclePersistenceError(OrchestrationLifecycleError):
    code = "orchestration_lifecycle_persistence_error"


class OrchestrationLifecycleStore(Protocol):
    async def start_planning(
        self,
        *,
        run_id: str,
        expected_revision: int,
        at: datetime,
    ) -> OrchestrationPlanningToken:
        """Atomically move one received Run to planning."""

        ...

    async def record_decision(
        self,
        *,
        token: OrchestrationPlanningToken,
        record: OrchestrationDecisionRecord,
        at: datetime,
    ) -> None:
        """Append safe Decision metadata while the planning revision is current."""

        ...

    async def fail_planning(
        self,
        *,
        token: OrchestrationPlanningToken,
        failure: OrchestrationFailureRecord,
        at: datetime,
    ) -> None:
        """Fail one planning Run without retrying its Root logical call."""

        ...

    async def complete_output(
        self,
        *,
        claim: RunClaim,
        output: SafeOutput,
        at: datetime,
    ) -> None:
        """Persist one SafeOutput and atomically complete its composing Run."""

        ...

    async def fail_composition(
        self,
        *,
        claim: RunClaim,
        error_code: str,
        at: datetime,
    ) -> None:
        """Fail one owned composing Run without persisting proposed Output."""

        ...


class RootDecisionMaker(Protocol):
    async def decide(self, request: RootOrchestrationRequest) -> RootOrchestrationResult:
        """Return one validated Root decision without semantic retries."""

        ...


class ExecutionPlanMaterializer(Protocol):
    async def materialize_and_enqueue(
        self,
        *,
        run_id: str,
        expected_revision: int,
        plan: PreparedExecutionPlan,
    ) -> ExecutionPlanSnapshot:
        """Persist one prepared Plan and move its Run to the durable queue."""

        ...


class ExecutionRunner(Protocol):
    async def execute(self, claim: RunClaim) -> ExecutionOutcome:
        """Execute one already claimed and persisted Plan."""

        ...


class SafeOutputComposer(Protocol):
    def compose(
        self,
        plan: PreparedExecutionPlan,
        outcome: ExecutionOutcome,
    ) -> SafeOutput:
        """Return only Output Guardrail-approved content."""

        ...
