"""Strict SQLite unit-of-work state machine."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from contextvars import Token
from enum import StrEnum
from types import TracebackType
from typing import TYPE_CHECKING, Self

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.errors import UnitOfWorkStateError

if TYPE_CHECKING:
    from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase


class _State(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    EXITED = "exited"


class SqliteUnitOfWork:
    """Serialize one explicit SQLite transaction on the runtime connection."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        self._state = _State.NEW
        self._guard: AbstractAsyncContextManager[None] | None = None
        self._context_token: Token[bool] | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        """Expose the connection only to concrete SQLite repository adapters."""

        if self._state is not _State.ACTIVE:
            raise UnitOfWorkStateError("unit of work is not active")
        return self._database._require_connection()

    async def __aenter__(self) -> Self:
        if self._state is not _State.NEW:
            raise UnitOfWorkStateError("unit of work cannot be reused")
        self._state = _State.EXITED
        token = self._database._claim_transaction_context()
        guard = self._database._write_coordinator.serialized()
        guard_acquired = False
        try:
            await guard.__aenter__()
            guard_acquired = True
            connection = self._database._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            try:
                if guard_acquired:
                    await guard.__aexit__(None, None, None)
            finally:
                self._database._release_transaction_context(token)
            raise
        self._context_token = token
        self._guard = guard
        self._state = _State.ACTIVE
        return self

    async def commit(self) -> None:
        connection = self.connection
        await connection.commit()
        self._state = _State.COMMITTED

    async def rollback(self) -> None:
        connection = self.connection
        await connection.rollback()
        self._state = _State.ROLLED_BACK

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._state not in {_State.ACTIVE, _State.COMMITTED, _State.ROLLED_BACK}:
            raise UnitOfWorkStateError("unit of work is not entered or already exited")
        guard = self._guard
        token = self._context_token
        if guard is None or token is None:
            raise UnitOfWorkStateError("unit of work resources are incomplete")
        try:
            if self._state is _State.ACTIVE:
                await self._database._require_connection().rollback()
                self._state = _State.ROLLED_BACK
        finally:
            self._state = _State.EXITED
            self._guard = None
            self._context_token = None
            try:
                await guard.__aexit__(exc_type, exc, traceback)
            finally:
                self._database._release_transaction_context(token)
