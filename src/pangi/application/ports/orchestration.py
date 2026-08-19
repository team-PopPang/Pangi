"""Consumer-owned ports used by Root orchestration."""

from __future__ import annotations

from typing import Protocol

from pangi.application.contracts.model_persistence import ModelInvocationContext
from pangi.application.contracts.model_routing import (
    GuardedModelExecution,
    ModelCallRequest,
)
from pangi.application.contracts.root_orchestration import RootCatalogSnapshot
from pangi.domain.runs import Principal


class RootCatalogProvider(Protocol):
    async def snapshot_for(self, principal: Principal) -> RootCatalogSnapshot:
        """Return one immutable, principal-scoped Catalog snapshot."""

        ...


class RootModelExecutor(Protocol):
    async def execute(
        self,
        request: ModelCallRequest,
        *,
        context: ModelInvocationContext,
    ) -> GuardedModelExecution:
        """Execute one governed logical Model call."""

        ...
