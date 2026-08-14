"""Single-connection SQLite runtime lifecycle."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import SqliteConnectionFactory
from pangi.adapters.outbound.persistence.sqlite.engine import SqliteMigrationAdmin
from pangi.adapters.outbound.persistence.sqlite.errors import UnitOfWorkStateError
from pangi.adapters.outbound.persistence.sqlite.locking import ProcessFileLock
from pangi.adapters.outbound.persistence.sqlite.write_coordinator import (
    SqliteWriteCoordinator,
)
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.ports.storage import MigrationAdmin
from pangi.config import StorageConfig

if TYPE_CHECKING:
    from pangi.adapters.outbound.persistence.sqlite.unit_of_work import (
        SqliteUnitOfWork,
    )


class SqliteDatabase:
    """Own one runtime connection, process lock, and write coordinator."""

    def __init__(
        self,
        paths: RuntimePaths,
        config: StorageConfig,
        *,
        migration_admin: MigrationAdmin | None = None,
        connection_factory: SqliteConnectionFactory | None = None,
    ) -> None:
        self.paths = paths
        self.config = config
        self._migration_admin = (
            SqliteMigrationAdmin(paths, config)
            if migration_admin is None
            else migration_admin
        )
        self._connections = (
            SqliteConnectionFactory(paths, config)
            if connection_factory is None
            else connection_factory
        )
        self._writes = SqliteWriteCoordinator()
        self._lifecycle_lock = asyncio.Lock()
        self._transaction_context: ContextVar[bool] = ContextVar(
            f"pangi_sqlite_transaction_{id(self)}",
            default=False,
        )
        self._connection: aiosqlite.Connection | None = None
        self._process_lock: ProcessFileLock | None = None
        self._closing = False

    @property
    def started(self) -> bool:
        return self._connection is not None and not self._closing

    async def start(self) -> None:
        """Apply migrations, acquire the process lock, and open one connection."""

        async with self._lifecycle_lock:
            if self._connection is not None:
                return
            await self._migration_admin.apply()
            process_lock = ProcessFileLock(self.paths.process_lock_file)
            process_lock.acquire()
            try:
                connection = await self._connections.open()
            except BaseException:
                process_lock.release()
                raise
            self._process_lock = process_lock
            self._connection = connection

    async def close(self) -> None:
        """Wait for the active writer, rollback leftovers, and release resources."""

        if self._transaction_context.get():
            raise UnitOfWorkStateError("cannot close SQLite inside an active unit of work")
        async with self._lifecycle_lock:
            connection = self._connection
            if connection is None:
                return
            self._closing = True
            try:
                async with self._writes.serialized():
                    process_lock = self._process_lock
                    try:
                        try:
                            await connection.rollback()
                        finally:
                            await connection.close()
                    finally:
                        self._connection = None
                        self._process_lock = None
                        self._closing = False
                        if process_lock is not None:
                            process_lock.release()
            except BaseException:
                self._closing = False
                raise

    def create(self) -> SqliteUnitOfWork:
        """Create one transaction boundary without exposing the connection."""

        from pangi.adapters.outbound.persistence.sqlite.unit_of_work import (
            SqliteUnitOfWork,
        )

        return SqliteUnitOfWork(self)

    def _require_connection(self) -> aiosqlite.Connection:
        connection = self._connection
        if connection is None or self._closing:
            raise UnitOfWorkStateError("SQLite runtime is not available")
        return connection

    def _claim_transaction_context(self) -> Token[bool]:
        if self._transaction_context.get():
            raise UnitOfWorkStateError("nested unit of work is not supported")
        return self._transaction_context.set(True)

    def _release_transaction_context(self, token: Token[bool]) -> None:
        self._transaction_context.reset(token)

    @property
    def _write_coordinator(self) -> SqliteWriteCoordinator:
        return self._writes
