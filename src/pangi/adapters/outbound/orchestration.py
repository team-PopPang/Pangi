"""Fail-closed execution adapter before concrete Subagents are registered."""

from pangi.application.contracts.orchestration import AgentResult
from pangi.application.contracts.orchestration_execution import StepExecutionRequest


class UnavailableOrchestrationTaskExecutor:
    async def execute(self, request: StepExecutionRequest) -> AgentResult:
        del request
        raise RuntimeError("Orchestration Task execution is unavailable")
