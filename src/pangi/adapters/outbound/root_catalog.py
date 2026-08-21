"""Fail-closed Root Catalog used before capability registries are available."""

from __future__ import annotations

from pangi.application.contracts.root_orchestration import RootCatalogSnapshot
from pangi.domain.runs import Principal

_EMPTY_ROOT_CATALOG = RootCatalogSnapshot(version="root-catalog-empty-v1")


class EmptyRootCatalogProvider:
    """Return a valid empty snapshot without inventing unavailable capabilities."""

    async def snapshot_for(self, principal: Principal) -> RootCatalogSnapshot:
        if not isinstance(principal, Principal):
            raise TypeError("principal must be a Principal")
        return _EMPTY_ROOT_CATALOG
