"""Checksum-verified SQLite migration planning and application."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.audit import SqliteAuditWriter
from pangi.adapters.outbound.persistence.sqlite.connection import (
    SqliteConnectionFactory,
    fetch_all,
    fetch_one,
    validate_storage_target,
)
from pangi.adapters.outbound.persistence.sqlite.errors import (
    MigrationApplyError,
    MigrationIntegrityError,
)
from pangi.adapters.outbound.persistence.sqlite.locking import ProcessFileLock
from pangi.adapters.outbound.persistence.sqlite.registry import (
    MigrationRegistry,
    MigrationSource,
    PackageMigrationRegistry,
)
from pangi.adapters.outbound.persistence.sqlite.snapshots import SqliteSnapshotStore
from pangi.adapters.outbound.persistence.sqlite.write_coordinator import (
    SqliteWriteCoordinator,
)
from pangi.application.contracts.audit import AuditEventDraft
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.contracts.snapshots import SnapshotKind
from pangi.application.contracts.storage import (
    MigrationApplyResult,
    MigrationDescriptor,
    MigrationPlan,
)
from pangi.application.services.audit import core_audit_redaction_service
from pangi.config import StorageConfig
from pangi.domain.audit import AuditOutcome

Now = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _sql_statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise MigrationIntegrityError("migration SQL contains an incomplete statement")
    return tuple(statements)


class SqliteMigrationAdmin:
    """Apply an immutable migration registry to one canonical SQLite file."""

    def __init__(
        self,
        paths: RuntimePaths,
        config: StorageConfig,
        *,
        registry: MigrationRegistry | None = None,
        now: Now = _utc_now,
        snapshot_store: SqliteSnapshotStore | None = None,
        audit_writer: SqliteAuditWriter | None = None,
    ) -> None:
        self.paths = paths
        self.config = config
        self._registry = PackageMigrationRegistry() if registry is None else registry
        self._connections = SqliteConnectionFactory(paths, config)
        self._writes = SqliteWriteCoordinator()
        self._now = now
        self._snapshots = (
            SqliteSnapshotStore(paths, registry=self._registry, now=now)
            if snapshot_store is None
            else snapshot_store
        )
        self._audit_writer = (
            SqliteAuditWriter(core_audit_redaction_service())
            if audit_writer is None
            else audit_writer
        )

    async def _read_applied(
        self,
        connection: aiosqlite.Connection,
    ) -> tuple[MigrationDescriptor, ...]:
        table = await fetch_one(
            connection,
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("schema_migrations",),
        )
        if table is None:
            tables = await fetch_all(
                connection,
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
            )
            if tables:
                raise MigrationIntegrityError(
                    "existing SQLite database is not managed by Pangi migrations"
                )
            return ()
        try:
            rows = await fetch_all(
                connection,
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version",
            )
        except aiosqlite.Error as error:
            raise MigrationIntegrityError("migration history table is invalid") from error
        return tuple(
            MigrationDescriptor(int(row["version"]), str(row["name"]), str(row["checksum"]))
            for row in rows
        )

    @staticmethod
    def _validate_applied(
        applied: tuple[MigrationDescriptor, ...],
        packaged: tuple[MigrationSource, ...],
    ) -> None:
        if len(applied) > len(packaged):
            raise MigrationIntegrityError("database schema is newer than this Pangi package")
        for index, recorded in enumerate(applied):
            expected = packaged[index].descriptor
            if recorded != expected:
                raise MigrationIntegrityError(
                    f"applied migration integrity check failed at version {recorded.version}"
                )

    async def plan(self) -> MigrationPlan:
        validate_storage_target(self.paths)
        packaged = self._registry.load()
        if not self.paths.database_file.exists():
            return MigrationPlan(self.paths.database_file, False, (), tuple(
                migration.descriptor for migration in packaged
            ))

        connection = await self._connections.open(read_only=True)
        try:
            applied = await self._read_applied(connection)
        except aiosqlite.Error as error:
            raise MigrationIntegrityError("SQLite migration history could not be read") from error
        finally:
            await connection.close()
        self._validate_applied(applied, packaged)
        pending = tuple(migration.descriptor for migration in packaged[len(applied) :])
        return MigrationPlan(self.paths.database_file, True, applied, pending)

    async def _create_backup(self, plan: MigrationPlan) -> Path:
        source = await self._connections.open(read_only=True)
        try:
            artifact = await self._snapshots.create(
                source,
                kind=SnapshotKind.PRE_MIGRATION,
                migration_target_version=plan.target_version,
            )
            return artifact.snapshot_file
        finally:
            await source.close()

    async def apply(self) -> MigrationApplyResult:
        async with self._writes.serialized():
            with ProcessFileLock(self.paths.process_lock_file):
                plan = await self.plan()
                if not plan.pending:
                    return MigrationApplyResult(
                        self.paths.database_file,
                        plan.current_version,
                        (),
                    )
                backup_file = await self._create_backup(plan) if plan.backup_required else None
                packaged = self._registry.load()
                pending = packaged[len(plan.applied) :]
                connection = await self._connections.open()
                failed_version = pending[0].descriptor.version
                try:
                    await connection.execute("PRAGMA foreign_keys=OFF")
                    await connection.execute("BEGIN IMMEDIATE")
                    for migration in pending:
                        failed_version = migration.descriptor.version
                        for statement in _sql_statements(migration.sql):
                            await connection.execute(statement)
                        await connection.execute(
                            "INSERT INTO schema_migrations "
                            "(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                            (
                                migration.descriptor.version,
                                migration.descriptor.name,
                                migration.descriptor.checksum,
                                self._now().astimezone(UTC).isoformat(),
                            ),
                        )
                    audit_table = await fetch_one(
                        connection,
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'audit_events'",
                    )
                    if audit_table is not None:
                        await self._audit_writer.insert(
                            connection,
                            AuditEventDraft(
                                actor_id="system.migration",
                                action="storage.migrations_applied",
                                resource_type="database_schema",
                                resource_id="primary",
                                outcome=AuditOutcome.SUCCEEDED,
                                created_at=self._now(),
                                before_summary={"version": plan.current_version},
                                after_summary={
                                    "version": plan.target_version,
                                    "migrations": [
                                        {
                                            "version": item.descriptor.version,
                                            "name": item.descriptor.name,
                                            "checksum": item.descriptor.checksum,
                                        }
                                        for item in pending
                                    ],
                                },
                                details={
                                    "migration_count": len(pending),
                                    "pre_migration_snapshot_created": backup_file is not None,
                                },
                            ),
                        )
                    foreign_key_violations = await fetch_all(
                        connection,
                        "PRAGMA foreign_key_check",
                    )
                    if foreign_key_violations:
                        raise MigrationIntegrityError(
                            "migration produced invalid foreign key references"
                        )
                    await connection.execute(f"PRAGMA user_version={plan.target_version}")
                    await connection.commit()
                    await connection.execute("PRAGMA foreign_keys=ON")
                except Exception as error:
                    await connection.rollback()
                    raise MigrationApplyError(
                        f"migration batch failed at version {failed_version}; changes rolled back"
                    ) from error
                finally:
                    try:
                        await connection.execute("PRAGMA foreign_keys=ON")
                    except aiosqlite.Error:
                        pass
                    await connection.close()
                return MigrationApplyResult(
                    self.paths.database_file,
                    plan.target_version,
                    tuple(migration.descriptor for migration in pending),
                    backup_file,
                )
