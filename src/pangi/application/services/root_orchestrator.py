"""Perform one Root decision without semantic retries or external execution."""

from __future__ import annotations

from pangi.application.contracts.model_persistence import ModelInvocationContext
from pangi.application.contracts.orchestration import OrchestratorDecision
from pangi.application.contracts.root_orchestration import (
    RootCatalogSnapshot,
    RootOrchestrationRequest,
    RootOrchestrationResult,
    RootOrchestratorPolicy,
)
from pangi.application.ports.orchestration import RootCatalogProvider, RootModelExecutor
from pangi.application.services.plan_validator import OrchestratorPlanValidator
from pangi.application.services.root_context import RootContextBuilder, RootDecisionParser
from pangi.domain.runs import RunMode


class RootCatalogUnavailableError(RuntimeError):
    code = "root_catalog_unavailable"

    def __init__(self) -> None:
        super().__init__("Root Catalog is unavailable")


class RootOrchestratorService:
    def __init__(
        self,
        policy: RootOrchestratorPolicy,
        *,
        catalogs: RootCatalogProvider,
        model: RootModelExecutor,
        context_builder: RootContextBuilder | None = None,
        parser: RootDecisionParser | None = None,
    ) -> None:
        self._policy = policy
        self._catalogs = catalogs
        self._model = model
        self._context_builder = context_builder or RootContextBuilder(policy)
        self._parser = parser or RootDecisionParser()

    async def decide(
        self,
        request: RootOrchestrationRequest,
    ) -> RootOrchestrationResult:
        catalog = await self._catalog_snapshot(request)
        validator = OrchestratorPlanValidator(
            catalog=catalog.validation_catalog,
            limits=self._policy.limits,
        )
        explicit_skill = request.guarded_request.request.explicit_skill
        if explicit_skill is not None:
            plan = validator.validate(
                OrchestratorDecision(
                    mode=RunMode.SKILL,
                    skill_name=explicit_skill,
                )
            )
            return RootOrchestrationResult(
                plan=plan,
                logical_call_count=0,
                provider_request_count=0,
            )

        logical_call_id = root_logical_call_id(request.run_id)
        model_request = self._context_builder.build(
            request,
            catalog=catalog,
            logical_call_id=logical_call_id,
        )
        execution = await self._model.execute(
            model_request,
            context=ModelInvocationContext(request.run_id),
        )
        decision = self._parser.parse(execution.response.canonical_output_json)
        plan = validator.validate(decision)
        return RootOrchestrationResult(
            plan=plan,
            logical_call_count=1,
            provider_request_count=execution.response.provider_request_count,
        )

    async def _catalog_snapshot(
        self,
        request: RootOrchestrationRequest,
    ) -> RootCatalogSnapshot:
        try:
            catalog = await self._catalogs.snapshot_for(request.guarded_request.request.principal)
        except Exception:
            raise RootCatalogUnavailableError() from None
        if not isinstance(catalog, RootCatalogSnapshot):
            raise RootCatalogUnavailableError()
        return catalog


def root_logical_call_id(run_id: str) -> str:
    return f"root-orchestration:{run_id}"
