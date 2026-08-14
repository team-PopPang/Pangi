"""Validated aiosqlite connection profile."""

from __future__ import annotations

from urllib.parse import quote

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.errors import StorageSafetyError
from pangi.adapters.outbound.persistence.sqlite.filesystem import ensure_local_filesystem
from pangi.application.contracts.paths import RuntimePaths
from pangi.config import StorageConfig


def validate_storage_target(paths: RuntimePaths) -> str | None:
    """Validate the canonical DB target and return its filesystem type."""

    data_dir = paths.data_dir.absolute()
    database_file = paths.database_file.absolute()
    if database_file.parent != data_dir:
        raise StorageSafetyError("SQLite database must be directly inside the data directory")
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise StorageSafetyError("SQLite data directory is missing or unsafe")
    if database_file.is_symlink() or (
        database_file.exists() and not database_file.is_file()
    ):
        raise StorageSafetyError("SQLite database target is unsafe")
    return ensure_local_filesystem(data_dir)


class SqliteConnectionFactory:
    """Open configured read-only or writable SQLite connections."""

    def __init__(self, paths: RuntimePaths, config: StorageConfig) -> None:
        self.paths = paths
        self.config = config

    async def open(self, *, read_only: bool = False) -> aiosqlite.Connection:
        validate_storage_target(self.paths)
        if read_only:
            encoded = quote(str(self.paths.database_file.absolute()), safe="/")
            connection = await aiosqlite.connect(
                f"file:{encoded}?mode=ro",
                uri=True,
                timeout=self.config.busy_timeout_ms / 1000,
                isolation_level=None,
            )
        else:
            connection = await aiosqlite.connect(
                self.paths.database_file,
                timeout=self.config.busy_timeout_ms / 1000,
                isolation_level=None,
            )
        try:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.execute(f"PRAGMA busy_timeout={self.config.busy_timeout_ms}")
            if not read_only:
                cursor = await connection.execute("PRAGMA journal_mode=DELETE")
                row = await cursor.fetchone()
                await cursor.close()
                if row is None or str(row[0]).lower() != self.config.journal_mode:
                    raise StorageSafetyError("SQLite journal mode could not be enforced")
            return connection
        except BaseException:
            await connection.close()
            raise


async def fetch_one(
    connection: aiosqlite.Connection,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> aiosqlite.Row | None:
    cursor = await connection.execute(statement, parameters)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


async def fetch_all(
    connection: aiosqlite.Connection,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> list[aiosqlite.Row]:
    cursor = await connection.execute(statement, parameters)
    try:
        return list(await cursor.fetchall())
    finally:
        await cursor.close()
