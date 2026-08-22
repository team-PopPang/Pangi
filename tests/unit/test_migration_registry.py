"""Packaged migration registry tests."""

import pytest

from pangi.adapters.outbound.persistence.sqlite.errors import MigrationIntegrityError
from pangi.adapters.outbound.persistence.sqlite.registry import (
    MigrationSource,
    PackageMigrationRegistry,
    StaticMigrationRegistry,
)


def test_packaged_registry_loads_consecutive_checksumed_migrations() -> None:
    migrations = PackageMigrationRegistry().load()

    assert [migration.descriptor.version for migration in migrations] == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
    ]
    assert migrations[0].descriptor.name == "schema_migrations"
    assert len(migrations[0].descriptor.checksum) == 64
    assert "CREATE TABLE schema_migrations" in migrations[0].sql
    assert migrations[1].descriptor.name == "auth_core"
    assert "CREATE TABLE auth_identities" in migrations[1].sql
    assert migrations[2].descriptor.name == "run_core"
    assert "CREATE TABLE run_events" in migrations[2].sql
    assert migrations[3].descriptor.name == "audit_events"
    assert "CREATE TABLE audit_events" in migrations[3].sql
    assert migrations[4].descriptor.name == "model_routing"
    assert migrations[5].descriptor.name == "model_policy_management"
    assert "CREATE TABLE model_invocations" in migrations[4].sql
    assert migrations[6].descriptor.name == "orchestration_execution"
    assert migrations[7].descriptor.name == "orchestration_lifecycle"
    assert "CREATE TABLE run_outputs" in migrations[7].sql
    assert migrations[8].descriptor.name == "connection_registry"
    assert "CREATE TABLE connection_tools" in migrations[8].sql
    assert migrations[9].descriptor.name == "tool_policy_budget"
    assert "CREATE TABLE tool_call_budgets" in migrations[9].sql
    assert migrations[10].descriptor.name == "tool_approvals"
    assert "CREATE TABLE tool_approvals" in migrations[10].sql


def test_static_registry_rejects_version_gaps() -> None:
    migration = MigrationSource.from_sql(2, "skipped", "SELECT 1;\n")

    with pytest.raises(MigrationIntegrityError, match="consecutive"):
        StaticMigrationRegistry(migration)
