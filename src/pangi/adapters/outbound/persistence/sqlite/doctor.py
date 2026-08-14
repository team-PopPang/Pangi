"""Read-only SQLite checks for the system doctor."""

from __future__ import annotations

import asyncio
import shutil

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import (
    SqliteConnectionFactory,
    fetch_one,
    validate_storage_target,
)
from pangi.adapters.outbound.persistence.sqlite.errors import StorageError
from pangi.adapters.outbound.persistence.sqlite.factory import build_migration_admin
from pangi.adapters.outbound.persistence.sqlite.filesystem import (
    NETWORK_FILESYSTEMS,
    detect_filesystem_type,
)
from pangi.adapters.outbound.persistence.sqlite.locking import process_lock_available
from pangi.adapters.outbound.persistence.sqlite.snapshots import SqliteSnapshotStore
from pangi.application.contracts.diagnostics import DiagnosticResult, DiagnosticStatus
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.services.doctor import DoctorCheck
from pangi.config import PangiConfig

_CHECK_IDS = (
    "sqlite.filesystem",
    "sqlite.database",
    "sqlite.profile",
    "sqlite.quick_check",
    "sqlite.schema",
    "sqlite.migrations",
    "sqlite.backup",
    "sqlite.process_lock",
    "sqlite.disk",
)


def _result(
    check_id: str,
    status: DiagnosticStatus,
    summary: str,
    next_command: str | None = None,
) -> DiagnosticResult:
    return DiagnosticResult(check_id, status, summary, next_command)


async def inspect_sqlite(
    paths: RuntimePaths,
    config: PangiConfig,
) -> tuple[DiagnosticResult, ...]:
    """Inspect SQLite without creating files or applying migrations."""

    results: dict[str, DiagnosticResult] = {}
    filesystem_type = detect_filesystem_type(paths.data_dir)
    if filesystem_type in NETWORK_FILESYSTEMS:
        results["sqlite.filesystem"] = _result(
            "sqlite.filesystem",
            DiagnosticStatus.FAIL,
            f"network filesystem is unsupported: {filesystem_type}",
            "move PANGI_HOME to a local filesystem",
        )
    elif filesystem_type is None:
        results["sqlite.filesystem"] = _result(
            "sqlite.filesystem",
            DiagnosticStatus.WARN,
            "filesystem type could not be determined",
        )
    else:
        results["sqlite.filesystem"] = _result(
            "sqlite.filesystem",
            DiagnosticStatus.PASS,
            f"local filesystem: {filesystem_type}",
        )

    try:
        validate_storage_target(paths)
    except (OSError, StorageError) as error:
        results["sqlite.database"] = _result(
            "sqlite.database",
            DiagnosticStatus.FAIL,
            str(error),
            "pangi init",
        )
    else:
        if paths.database_file.exists():
            results["sqlite.database"] = _result(
                "sqlite.database",
                DiagnosticStatus.PASS,
                f"database file is readable: {paths.database_file}",
            )
        else:
            results["sqlite.database"] = _result(
                "sqlite.database",
                DiagnosticStatus.FAIL,
                f"database file does not exist: {paths.database_file}",
                "pangi migrate apply --yes",
            )

    if paths.database_file.is_file() and not paths.database_file.is_symlink():
        factory = SqliteConnectionFactory(paths, config.storage)
        connection: aiosqlite.Connection | None = None
        try:
            connection = await factory.open(read_only=True)
            journal = await fetch_one(connection, "PRAGMA journal_mode")
            foreign_keys = await fetch_one(connection, "PRAGMA foreign_keys")
            busy_timeout = await fetch_one(connection, "PRAGMA busy_timeout")
            profile_ok = (
                journal is not None
                and str(journal[0]).lower() == config.storage.journal_mode
                and foreign_keys is not None
                and int(foreign_keys[0]) == 1
                and busy_timeout is not None
                and int(busy_timeout[0]) == config.storage.busy_timeout_ms
            )
            results["sqlite.profile"] = _result(
                "sqlite.profile",
                DiagnosticStatus.PASS if profile_ok else DiagnosticStatus.FAIL,
                "DELETE journal, foreign keys and busy timeout are active"
                if profile_ok
                else "SQLite connection profile does not match configuration",
                None if profile_ok else "pangi config validate",
            )
            quick_check = await fetch_one(connection, "PRAGMA quick_check")
            quick_ok = quick_check is not None and str(quick_check[0]).lower() == "ok"
            results["sqlite.quick_check"] = _result(
                "sqlite.quick_check",
                DiagnosticStatus.PASS if quick_ok else DiagnosticStatus.FAIL,
                "quick_check: ok" if quick_ok else "quick_check failed",
                None if quick_ok else "restore a verified SQLite backup",
            )
        except (OSError, StorageError, aiosqlite.Error):
            results["sqlite.profile"] = _result(
                "sqlite.profile",
                DiagnosticStatus.FAIL,
                "SQLite connection profile could not be verified",
            )
            results["sqlite.quick_check"] = _result(
                "sqlite.quick_check",
                DiagnosticStatus.FAIL,
                "SQLite quick_check could not run",
            )
        finally:
            if connection is not None:
                await connection.close()
    else:
        results["sqlite.profile"] = _result(
            "sqlite.profile",
            DiagnosticStatus.SKIP,
            "SQLite database is not available",
        )
        results["sqlite.quick_check"] = _result(
            "sqlite.quick_check",
            DiagnosticStatus.SKIP,
            "SQLite database is not available",
        )

    try:
        plan = await build_migration_admin(paths, config).plan()
    except (OSError, StorageError) as error:
        results["sqlite.schema"] = _result(
            "sqlite.schema",
            DiagnosticStatus.FAIL,
            str(error),
        )
        results["sqlite.migrations"] = _result(
            "sqlite.migrations",
            DiagnosticStatus.FAIL,
            "migration integrity could not be verified",
        )
    else:
        schema_ok = plan.database_exists and plan.current_version > 0
        results["sqlite.schema"] = _result(
            "sqlite.schema",
            DiagnosticStatus.PASS if schema_ok else DiagnosticStatus.FAIL,
            f"schema version {plan.current_version}",
            None if schema_ok else "pangi migrate apply --yes",
        )
        results["sqlite.migrations"] = _result(
            "sqlite.migrations",
            DiagnosticStatus.FAIL if plan.pending else DiagnosticStatus.PASS,
            f"{len(plan.pending)} pending migration(s)"
            if plan.pending
            else "package and database migrations match",
            "pangi migrate apply --yes" if plan.pending else None,
        )

    try:
        snapshot_store = SqliteSnapshotStore(paths)
        latest_manifest, has_legacy = snapshot_store.latest_manifest()
        if latest_manifest is None:
            results["sqlite.backup"] = _result(
                "sqlite.backup",
                DiagnosticStatus.WARN if has_legacy else DiagnosticStatus.SKIP,
                "unverified legacy SQLite backup exists"
                if has_legacy
                else "no SQLite snapshot has been created",
            )
        else:
            verification = await snapshot_store.verify(latest_manifest)
            compatible = verification.package_compatible
            results["sqlite.backup"] = _result(
                "sqlite.backup",
                DiagnosticStatus.PASS if compatible else DiagnosticStatus.WARN,
                "latest SQLite snapshot is verified"
                if compatible
                else "latest SQLite snapshot is verified but needs compatibility review",
            )
    except (OSError, StorageError):
        results["sqlite.backup"] = _result(
            "sqlite.backup",
            DiagnosticStatus.FAIL,
            "latest SQLite snapshot failed integrity verification",
        )

    try:
        available = process_lock_available(paths.process_lock_file)
    except (OSError, StorageError) as error:
        results["sqlite.process_lock"] = _result(
            "sqlite.process_lock",
            DiagnosticStatus.FAIL,
            str(error),
        )
    else:
        results["sqlite.process_lock"] = _result(
            "sqlite.process_lock",
            DiagnosticStatus.PASS if available else DiagnosticStatus.FAIL,
            "process lock is available" if available else "another Pangi process owns the lock",
            None if available else "pangi status",
        )

    try:
        usage = shutil.disk_usage(paths.data_dir)
        database_size = paths.database_file.stat().st_size if paths.database_file.exists() else 0
        disk_ok = usage.free > 0
        results["sqlite.disk"] = _result(
            "sqlite.disk",
            DiagnosticStatus.PASS if disk_ok else DiagnosticStatus.FAIL,
            f"database {database_size} bytes; disk free {usage.free} bytes",
        )
    except OSError:
        results["sqlite.disk"] = _result(
            "sqlite.disk",
            DiagnosticStatus.FAIL,
            "database size or free disk space could not be read",
        )

    return tuple(results[check_id] for check_id in _CHECK_IDS)


class _SqliteCheckCache:
    def __init__(self, paths: RuntimePaths, config: PangiConfig) -> None:
        self._paths = paths
        self._config = config
        self._results: dict[str, DiagnosticResult] | None = None

    def get(self, check_id: str) -> DiagnosticResult:
        if self._results is None:
            inspected = asyncio.run(inspect_sqlite(self._paths, self._config))
            self._results = {result.check_id: result for result in inspected}
        return self._results[check_id]


def build_sqlite_doctor_checks(
    paths: RuntimePaths,
    config: PangiConfig,
) -> tuple[DoctorCheck, ...]:
    """Build ordered, lazily evaluated SQLite doctor checks."""

    cache = _SqliteCheckCache(paths, config)

    def build_check(check_id: str) -> DoctorCheck:
        def run() -> DiagnosticResult:
            return cache.get(check_id)

        return DoctorCheck(check_id, run)

    return tuple(build_check(check_id) for check_id in _CHECK_IDS)
