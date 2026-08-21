"""Fail-closed Root Catalog adapter tests."""

from __future__ import annotations

import asyncio

import pytest

from pangi.adapters.outbound.root_catalog import EmptyRootCatalogProvider
from pangi.domain.auth import UserRole
from pangi.domain.runs import Principal, PrincipalChannel


def _principal(user_id: str) -> Principal:
    return Principal(user_id, UserRole.MEMBER, PrincipalChannel.API)


def test_empty_catalog_is_immutable_valid_and_contains_no_invented_capabilities() -> None:
    provider = EmptyRootCatalogProvider()

    first = asyncio.run(provider.snapshot_for(_principal("member-catalog-0001")))
    second = asyncio.run(provider.snapshot_for(_principal("member-catalog-0002")))

    assert first == second
    assert first.version == "root-catalog-empty-v1"
    assert first.subagents == ()
    assert first.skills == ()
    assert first.connection_names == ()
    assert first.validation_catalog.available_subagents == frozenset()
    assert first.validation_catalog.active_skills == frozenset()


def test_empty_catalog_rejects_an_untrusted_principal_shape() -> None:
    provider = EmptyRootCatalogProvider()

    with pytest.raises(TypeError, match="principal"):
        asyncio.run(provider.snapshot_for(object()))  # type: ignore[arg-type]
