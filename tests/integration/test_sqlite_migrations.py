"""SQLite migration and safety integration tests."""

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.connection import (
    SqliteConnectionFactory,
    fetch_one,
)
from pangi.adapters.outbound.persistence.sqlite.engine import SqliteMigrationAdmin
from pangi.adapters.outbound.persistence.sqlite.errors import (
    MigrationApplyError,
    MigrationIntegrityError,
    SnapshotError,
)
from pangi.adapters.outbound.persistence.sqlite.registry import (
    MigrationSource,
    PackageMigrationRegistry,
    StaticMigrationRegistry,
)
from pangi.adapters.outbound.persistence.sqlite.snapshots import SqliteSnapshotStore
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.paths import RuntimePaths


def _initialized_runtime(tmp_path: Path) -> tuple[RuntimePaths, PangiConfig]:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    return paths, config


def test_plan_is_read_only_and_apply_is_idempotent(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    admin = SqliteMigrationAdmin(paths, config.storage)

    plan = asyncio.run(admin.plan())
    assert not paths.database_file.exists()
    first = asyncio.run(admin.apply())
    second = asyncio.run(admin.apply())

    assert not plan.database_exists
    assert [migration.version for migration in plan.pending] == [1, 2, 3, 4, 5, 6, 7]
    assert paths.database_file.exists()
    assert first.current_version == 7
    assert [migration.version for migration in first.applied] == [1, 2, 3, 4, 5, 6, 7]
    assert second.current_version == 7
    assert second.applied == ()
    assert second.backup_file is None


def test_sqlite_connection_profile_is_enforced(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    asyncio.run(SqliteMigrationAdmin(paths, config.storage).apply())

    async def inspect_profile() -> tuple[str, int, int, int]:
        connection = await SqliteConnectionFactory(paths, config.storage).open(read_only=True)
        try:
            journal = await fetch_one(connection, "PRAGMA journal_mode")
            foreign_keys = await fetch_one(connection, "PRAGMA foreign_keys")
            busy_timeout = await fetch_one(connection, "PRAGMA busy_timeout")
            user_version = await fetch_one(connection, "PRAGMA user_version")
            assert journal is not None
            assert foreign_keys is not None
            assert busy_timeout is not None
            assert user_version is not None
            return (
                str(journal[0]),
                int(foreign_keys[0]),
                int(busy_timeout[0]),
                int(user_version[0]),
            )
        finally:
            await connection.close()

    assert asyncio.run(inspect_profile()) == ("delete", 1, 5000, 7)


def test_applied_migration_checksum_change_is_rejected(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    packaged = PackageMigrationRegistry().load()
    first = packaged[0]
    asyncio.run(
        SqliteMigrationAdmin(
            paths,
            config.storage,
            registry=StaticMigrationRegistry(first),
        ).apply()
    )
    changed = MigrationSource.from_sql(
        1,
        packaged[0].descriptor.name,
        packaged[0].sql + "\n-- changed after release\n",
    )
    admin = SqliteMigrationAdmin(
        paths,
        config.storage,
        registry=StaticMigrationRegistry(changed),
    )

    with pytest.raises(MigrationIntegrityError, match="integrity check failed"):
        asyncio.run(admin.plan())


def test_pending_batch_rolls_back_all_schema_changes_on_failure(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    first = PackageMigrationRegistry().load()[0]
    broken = MigrationSource.from_sql(
        2,
        "broken_batch",
        "CREATE TABLE rollback_probe (id INTEGER PRIMARY KEY);\n"
        "CREATE TABL invalid_statement (id INTEGER);\n",
    )
    admin = SqliteMigrationAdmin(
        paths,
        config.storage,
        registry=StaticMigrationRegistry(first, broken),
    )

    with pytest.raises(MigrationApplyError, match="changes rolled back"):
        asyncio.run(admin.apply())

    with sqlite3.connect(paths.database_file) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    assert tables == []


def test_existing_database_is_backed_up_before_pending_migration(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    first = PackageMigrationRegistry().load()[0]
    asyncio.run(
        SqliteMigrationAdmin(
            paths,
            config.storage,
            registry=StaticMigrationRegistry(first),
        ).apply()
    )
    second = MigrationSource.from_sql(
        2,
        "backup_probe",
        "CREATE TABLE backup_probe (id INTEGER PRIMARY KEY);\n",
    )
    admin = SqliteMigrationAdmin(
        paths,
        config.storage,
        registry=StaticMigrationRegistry(first, second),
        now=lambda: datetime(2026, 8, 15, 0, 0, tzinfo=UTC),
    )

    result = asyncio.run(admin.apply())

    assert result.current_version == 2
    assert result.backup_file is not None
    assert result.backup_file.name == "pre-migrate-v1-to-v2-20260815T000000Z.sqlite3"
    assert result.backup_file.stat().st_mode & 0o777 == 0o600
    manifest_file = result.backup_file.with_name(f"{result.backup_file.name}.manifest.json")
    verification = asyncio.run(
        SqliteSnapshotStore(
            paths,
            registry=StaticMigrationRegistry(first, second),
        ).verify(manifest_file)
    )
    assert verification.package_compatible
    assert verification.artifact.manifest.migration_target_version == 2
    with sqlite3.connect(result.backup_file) as backup:
        assert backup.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert backup.execute("PRAGMA user_version").fetchone() == (1,)


def test_packaged_auth_migration_upgrades_v1_with_verified_backup(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    packaged = PackageMigrationRegistry().load()
    first, auth = packaged[:2]
    asyncio.run(
        SqliteMigrationAdmin(
            paths,
            config.storage,
            registry=StaticMigrationRegistry(first),
        ).apply()
    )

    result = asyncio.run(
        SqliteMigrationAdmin(
            paths,
            config.storage,
            registry=StaticMigrationRegistry(first, auth),
        ).apply()
    )

    assert result.current_version == 2
    assert [migration.name for migration in result.applied] == ["auth_core"]
    assert result.backup_file is not None
    with sqlite3.connect(paths.database_file) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    assert {"users", "auth_identities", "auth_sessions", "bootstrap_grants"} <= tables
    with sqlite3.connect(result.backup_file) as backup:
        assert backup.execute("PRAGMA user_version").fetchone() == (1,)
        assert (
            backup.execute("SELECT name FROM sqlite_master WHERE name = 'users'").fetchone() is None
        )


def test_packaged_run_core_migration_upgrades_v2_with_verified_backup(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    packaged = PackageMigrationRegistry().load()
    (
        first,
        auth,
        run_core,
        audit,
        model_routing,
        model_policy_management,
        orchestration_execution,
    ) = packaged
    asyncio.run(
        SqliteMigrationAdmin(
            paths,
            config.storage,
            registry=StaticMigrationRegistry(first, auth),
        ).apply()
    )

    result = asyncio.run(SqliteMigrationAdmin(paths, config.storage).apply())

    assert result.current_version == 7
    assert [migration.name for migration in result.applied] == [
        run_core.descriptor.name,
        audit.descriptor.name,
        model_routing.descriptor.name,
        model_policy_management.descriptor.name,
        orchestration_execution.descriptor.name,
    ]
    assert result.backup_file is not None
    manifest_file = result.backup_file.with_name(f"{result.backup_file.name}.manifest.json")
    verification = asyncio.run(SqliteSnapshotStore(paths).verify(manifest_file))
    assert verification.package_compatible
    assert verification.artifact.manifest.migration_target_version == 7
    with sqlite3.connect(paths.database_file) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
    assert {
        "runs",
        "run_steps",
        "run_events",
        "api_idempotency_records",
        "audit_events",
        "model_policies",
        "model_invocations",
        "run_execution_plans",
    } <= tables
    with sqlite3.connect(result.backup_file) as backup:
        assert backup.execute("PRAGMA user_version").fetchone() == (2,)
        assert (
            backup.execute("SELECT name FROM sqlite_master WHERE name = 'runs'").fetchone() is None
        )


def test_auth_schema_enforces_roles_identity_shape_and_single_open_grant(
    tmp_path: Path,
) -> None:
    paths, config = _initialized_runtime(tmp_path)
    asyncio.run(SqliteMigrationAdmin(paths, config.storage).apply())
    timestamp = datetime(2026, 8, 15, 0, 0, tzinfo=UTC).isoformat()
    with sqlite3.connect(paths.database_file) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                ("invalid-user-0001", "Invalid", "owner", "active", timestamp, timestamp),
            )
        connection.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
            ("member-user-00001", "Member", "member", "active", timestamp, timestamp),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO auth_identities VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "slack-identity-0001",
                    "member-user-00001",
                    "slack",
                    "U123",
                    "$argon2id$must-not-exist",
                    timestamp,
                    timestamp,
                ),
            )
        connection.execute(
            "INSERT INTO bootstrap_grants VALUES (?, ?, ?, NULL, NULL, NULL, ?)",
            ("bootstrap-grant-001", "a" * 64, timestamp, timestamp),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bootstrap_grants VALUES (?, ?, ?, NULL, NULL, NULL, ?)",
                ("bootstrap-grant-002", "b" * 64, timestamp, timestamp),
            )


def test_snapshot_failure_prevents_pending_migration(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    first = PackageMigrationRegistry().load()[0]
    asyncio.run(
        SqliteMigrationAdmin(
            paths,
            config.storage,
            registry=StaticMigrationRegistry(first),
        ).apply()
    )
    second = MigrationSource.from_sql(
        2,
        "must_not_apply",
        "CREATE TABLE must_not_apply (id INTEGER PRIMARY KEY);\n",
    )
    registry = StaticMigrationRegistry(first, second)

    async def fail_backup(
        _source: aiosqlite.Connection,
        _target: aiosqlite.Connection,
    ) -> None:
        raise RuntimeError("injected snapshot failure")

    snapshots = SqliteSnapshotStore(paths, registry=registry, backup=fail_backup)
    admin = SqliteMigrationAdmin(
        paths,
        config.storage,
        registry=registry,
        snapshot_store=snapshots,
    )

    with pytest.raises(SnapshotError, match="creation failed"):
        asyncio.run(admin.apply())

    assert list(paths.backup_dir.iterdir()) == []
    with sqlite3.connect(paths.database_file) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'must_not_apply'"
            ).fetchone()
            is None
        )
