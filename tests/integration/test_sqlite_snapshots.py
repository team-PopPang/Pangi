"""SQLite snapshot creation, verification, and diagnostic integration tests."""

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.connection import SqliteConnectionFactory
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.doctor import inspect_sqlite
from pangi.adapters.outbound.persistence.sqlite.engine import SqliteMigrationAdmin
from pangi.adapters.outbound.persistence.sqlite.errors import (
    SnapshotError,
    SnapshotIntegrityError,
    StorageSafetyError,
    UnitOfWorkStateError,
)
from pangi.adapters.outbound.persistence.sqlite.snapshots import SqliteSnapshotStore
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.contracts.snapshots import SnapshotKind


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


async def _create_probe_table(database: SqliteDatabase) -> None:
    async with database.create() as unit_of_work:
        await unit_of_work.connection.execute(
            "CREATE TABLE snapshot_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        await unit_of_work.commit()


def test_runtime_snapshot_has_private_portable_manifest_and_committed_data(
    tmp_path: Path,
) -> None:
    paths, config = _initialized_runtime(tmp_path)
    database = SqliteDatabase(paths, config.storage)

    async def exercise() -> None:
        await database.start()
        try:
            await _create_probe_table(database)
            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "INSERT INTO snapshot_probe (value) VALUES ('committed')"
                )
                await unit_of_work.commit()

            artifact = await database.create_snapshot()
            verification = await database.verify_snapshot(artifact.manifest_file)

            assert verification.package_compatible
            assert artifact.snapshot_file.stat().st_mode & 0o777 == 0o600
            assert artifact.manifest_file.stat().st_mode & 0o777 == 0o600
            assert artifact.manifest.snapshot_file == artifact.snapshot_file.name
            assert artifact.manifest.migration_target_version is None
            manifest_text = artifact.manifest_file.read_text("utf-8")
            assert str(paths.root) not in manifest_text
            assert str(paths.config_file) not in manifest_text

            with sqlite3.connect(artifact.snapshot_file) as snapshot:
                assert snapshot.execute("PRAGMA quick_check").fetchone() == ("ok",)
                assert snapshot.execute(
                    "SELECT value FROM snapshot_probe"
                ).fetchall() == [("committed",)]
        finally:
            await database.close()

    asyncio.run(exercise())


def test_runtime_snapshot_waits_for_active_write_and_rejects_nested_use(
    tmp_path: Path,
) -> None:
    paths, config = _initialized_runtime(tmp_path)
    database = SqliteDatabase(paths, config.storage)

    async def exercise() -> None:
        await database.start()
        try:
            await _create_probe_table(database)
            entered = asyncio.Event()
            release = asyncio.Event()

            async def writer() -> None:
                async with database.create() as unit_of_work:
                    await unit_of_work.connection.execute(
                        "INSERT INTO snapshot_probe (value) VALUES ('serialized')"
                    )
                    with pytest.raises(UnitOfWorkStateError, match="cannot snapshot"):
                        await database.create_snapshot()
                    entered.set()
                    await release.wait()
                    await unit_of_work.commit()

            writer_task = asyncio.create_task(writer())
            await entered.wait()
            snapshot_task = asyncio.create_task(database.create_snapshot())
            await asyncio.sleep(0)
            assert not snapshot_task.done()

            release.set()
            await writer_task
            artifact = await snapshot_task
            with sqlite3.connect(artifact.snapshot_file) as snapshot:
                assert snapshot.execute(
                    "SELECT value FROM snapshot_probe"
                ).fetchall() == [("serialized",)]
        finally:
            await database.close()

    asyncio.run(exercise())


def test_snapshot_verification_rejects_tampering_and_path_escape(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    database = SqliteDatabase(paths, config.storage)

    async def exercise() -> None:
        await database.start()
        try:
            first = await database.create_snapshot()
            with first.snapshot_file.open("ab") as snapshot:
                snapshot.write(b"tampered")
            with pytest.raises(SnapshotIntegrityError, match="checksum or size"):
                await database.verify_snapshot(first.manifest_file)

            second = await database.create_snapshot()
            payload = json.loads(second.manifest_file.read_text("utf-8"))
            payload["snapshot"]["file"] = "../escape.sqlite3"
            second.manifest_file.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                "utf-8",
            )
            second.manifest_file.chmod(0o600)
            with pytest.raises(SnapshotIntegrityError, match="file name is unsafe"):
                await database.verify_snapshot(second.manifest_file)

            third = await database.create_snapshot()
            moved = tmp_path / "moved-snapshot.sqlite3"
            third.snapshot_file.replace(moved)
            third.snapshot_file.symlink_to(moved)
            with pytest.raises(StorageSafetyError, match="path is unsafe"):
                await database.verify_snapshot(third.manifest_file)

            fourth = await database.create_snapshot()
            with sqlite3.connect(fourth.snapshot_file) as snapshot:
                snapshot.execute(
                    "UPDATE schema_migrations SET checksum = ? WHERE version = 1",
                    ("0" * 64,),
                )
                snapshot.commit()
            payload = json.loads(fourth.manifest_file.read_text("utf-8"))
            payload["snapshot"]["sha256"] = hashlib.sha256(
                fourth.snapshot_file.read_bytes()
            ).hexdigest()
            payload["snapshot"]["size_bytes"] = fourth.snapshot_file.stat().st_size
            fourth.manifest_file.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                "utf-8",
            )
            fourth.manifest_file.chmod(0o600)
            with pytest.raises(SnapshotIntegrityError, match="schema history"):
                await database.verify_snapshot(fourth.manifest_file)
        finally:
            await database.close()

    asyncio.run(exercise())


def test_snapshot_failure_and_cancellation_remove_partial_artifacts(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    asyncio.run(SqliteMigrationAdmin(paths, config.storage).apply())

    async def exercise() -> None:
        source = await SqliteConnectionFactory(paths, config.storage).open(read_only=True)
        try:
            async def fail_backup(
                _source: aiosqlite.Connection,
                _target: aiosqlite.Connection,
            ) -> None:
                raise RuntimeError("injected backup failure")

            failing = SqliteSnapshotStore(paths, backup=fail_backup)
            with pytest.raises(SnapshotError, match="creation failed"):
                await failing.create(source, kind=SnapshotKind.RUNTIME)
            assert list(paths.backup_dir.iterdir()) == []

            entered = asyncio.Event()
            blocked = asyncio.Event()

            async def block_backup(
                _source: aiosqlite.Connection,
                _target: aiosqlite.Connection,
            ) -> None:
                entered.set()
                await blocked.wait()

            cancelling = SqliteSnapshotStore(paths, backup=block_backup)
            task = asyncio.create_task(
                cancelling.create(source, kind=SnapshotKind.RUNTIME)
            )
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert list(paths.backup_dir.iterdir()) == []
        finally:
            await source.close()

    asyncio.run(exercise())


def test_doctor_reports_snapshot_absent_valid_and_corrupted(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    database = SqliteDatabase(paths, config.storage)

    async def backup_status() -> str:
        results = await inspect_sqlite(paths, config)
        return next(result for result in results if result.check_id == "sqlite.backup").status.value

    async def exercise() -> None:
        await database.start()
        await database.close()
        assert await backup_status() == "SKIP"

        await database.start()
        artifact = await database.create_snapshot()
        await database.close()
        assert await backup_status() == "PASS"

        with artifact.snapshot_file.open("ab") as snapshot:
            snapshot.write(b"tampered")
        assert await backup_status() == "FAIL"

    asyncio.run(exercise())
