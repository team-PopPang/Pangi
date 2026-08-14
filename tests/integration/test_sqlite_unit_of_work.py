"""SQLite runtime lifecycle and unit-of-work integration tests."""

import asyncio
from pathlib import Path

import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.errors import (
    MigrationApplyError,
    StorageBusyError,
    UnitOfWorkStateError,
)
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


async def _create_probe_table(database: SqliteDatabase) -> None:
    async with database.create() as unit_of_work:
        await unit_of_work.connection.execute(
            "CREATE TABLE uow_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        await unit_of_work.commit()


async def _probe_values(database: SqliteDatabase) -> list[str]:
    async with database.create() as unit_of_work:
        cursor = await unit_of_work.connection.execute(
            "SELECT value FROM uow_probe ORDER BY id"
        )
        try:
            return [str(row[0]) for row in await cursor.fetchall()]
        finally:
            await cursor.close()


def test_start_applies_migrations_once_and_close_is_idempotent(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    database = SqliteDatabase(paths, config.storage)

    async def exercise() -> None:
        await database.start()
        await database.start()
        assert database.started
        async with database.create() as unit_of_work:
            runtime_connection = unit_of_work.connection
            row = await fetch_one(unit_of_work.connection, "PRAGMA user_version")
            assert row is not None
            assert int(row[0]) == 1
        async with database.create() as unit_of_work:
            assert unit_of_work.connection is runtime_connection
        await database.close()
        await database.close()
        assert not database.started

    asyncio.run(exercise())


def test_commit_persists_and_unfinished_work_rolls_back(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    database = SqliteDatabase(paths, config.storage)

    async def exercise() -> None:
        await database.start()
        try:
            await _create_probe_table(database)
            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "INSERT INTO uow_probe (value) VALUES (?)",
                    ("committed",),
                )
                await unit_of_work.commit()
            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "INSERT INTO uow_probe (value) VALUES (?)",
                    ("unfinished",),
                )
            assert await _probe_values(database) == ["committed"]
        finally:
            await database.close()

    asyncio.run(exercise())


def test_explicit_rollback_exception_and_cancellation_discard_changes(
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
                    "INSERT INTO uow_probe (value) VALUES ('explicit')"
                )
                await unit_of_work.rollback()

            with pytest.raises(RuntimeError, match="application failed"):
                async with database.create() as unit_of_work:
                    await unit_of_work.connection.execute(
                        "INSERT INTO uow_probe (value) VALUES ('exception')"
                    )
                    raise RuntimeError("application failed")

            with pytest.raises(asyncio.CancelledError):
                async with database.create() as unit_of_work:
                    await unit_of_work.connection.execute(
                        "INSERT INTO uow_probe (value) VALUES ('cancelled')"
                    )
                    raise asyncio.CancelledError

            assert await _probe_values(database) == []
        finally:
            await database.close()

    asyncio.run(exercise())


def test_unit_of_work_rejects_nested_reused_and_double_completion(
    tmp_path: Path,
) -> None:
    paths, config = _initialized_runtime(tmp_path)
    database = SqliteDatabase(paths, config.storage)

    async def exercise() -> None:
        await database.start()
        reusable = database.create()
        with pytest.raises(UnitOfWorkStateError, match="not active"):
            await reusable.commit()

        async with reusable as outer:
            with pytest.raises(UnitOfWorkStateError, match="nested"):
                async with database.create():
                    pass
            with pytest.raises(UnitOfWorkStateError, match="cannot close"):
                await database.close()
            await outer.commit()
            with pytest.raises(UnitOfWorkStateError, match="not active"):
                await outer.commit()

        with pytest.raises(UnitOfWorkStateError, match="cannot be reused"):
            async with reusable:
                pass
        await database.close()

    asyncio.run(exercise())


def test_concurrent_writes_are_serialized_on_one_connection(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    database = SqliteDatabase(paths, config.storage)

    async def exercise() -> None:
        await database.start()
        try:
            await _create_probe_table(database)

            async def insert(value: int) -> None:
                async with database.create() as unit_of_work:
                    await unit_of_work.connection.execute(
                        "INSERT INTO uow_probe (id, value) VALUES (?, ?)",
                        (value, str(value)),
                    )
                    await asyncio.sleep(0)
                    await unit_of_work.commit()

            await asyncio.gather(*(insert(value) for value in range(20)))
            assert await _probe_values(database) == [str(value) for value in range(20)]
        finally:
            await database.close()

    asyncio.run(exercise())


def test_second_runtime_is_rejected_while_process_lock_is_held(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    first = SqliteDatabase(paths, config.storage)
    second = SqliteDatabase(paths, config.storage)

    async def exercise() -> None:
        await first.start()
        try:
            with pytest.raises(StorageBusyError, match="already owned"):
                await second.start()
            assert not second.started
        finally:
            await first.close()

        await second.start()
        await second.close()

    asyncio.run(exercise())


def test_migration_failure_prevents_runtime_start(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)

    class FailingMigrationAdmin:
        async def apply(self) -> None:
            raise MigrationApplyError("migration failed")

    database = SqliteDatabase(
        paths,
        config.storage,
        migration_admin=FailingMigrationAdmin(),  # type: ignore[arg-type]
    )

    with pytest.raises(MigrationApplyError, match="migration failed"):
        asyncio.run(database.start())
    assert not database.started
    assert not paths.process_lock_file.exists()
