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

    assert [migration.descriptor.version for migration in migrations] == [1, 2, 3]
    assert migrations[0].descriptor.name == "schema_migrations"
    assert len(migrations[0].descriptor.checksum) == 64
    assert "CREATE TABLE schema_migrations" in migrations[0].sql
    assert migrations[1].descriptor.name == "auth_core"
    assert "CREATE TABLE auth_identities" in migrations[1].sql
    assert migrations[2].descriptor.name == "run_core"
    assert "CREATE TABLE run_events" in migrations[2].sql


def test_static_registry_rejects_version_gaps() -> None:
    migration = MigrationSource.from_sql(2, "skipped", "SELECT 1;\n")

    with pytest.raises(MigrationIntegrityError, match="consecutive"):
        StaticMigrationRegistry(migration)
