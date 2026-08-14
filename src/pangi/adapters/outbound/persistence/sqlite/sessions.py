"""SQLite persistence for Local Identities and browser Sessions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.application.contracts.auth import (
    AuthenticatedPrincipal,
    LocalIdentityRecord,
    PasswordHashUpdate,
    StoredAuthSession,
)
from pangi.domain.auth import (
    AuthSession,
    IdentityProvider,
    SessionState,
    UserRole,
    UserStatus,
)


class SqliteAuthSessionStore:
    """Serialize authentication reads and writes on the runtime connection."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    @asynccontextmanager
    async def _runtime(self) -> AsyncIterator[None]:
        started_here = not self._database.started
        if started_here:
            await self._database.start()
        try:
            yield
        finally:
            if started_here:
                await self._database.close()

    async def find_local_identity(self, *, subject: str) -> LocalIdentityRecord | None:
        async with self._runtime(), self._database.create() as unit_of_work:
            row = await fetch_one(
                unit_of_work.connection,
                "SELECT i.id AS identity_id, i.password_hash, "
                "u.id AS user_id, u.display_name, u.role, u.status "
                "FROM auth_identities i JOIN users u ON u.id = i.user_id "
                "WHERE i.provider = ? AND i.subject = ?",
                (IdentityProvider.LOCAL.value, subject),
            )
            await unit_of_work.commit()
        if row is None or row["password_hash"] is None:
            return None
        return LocalIdentityRecord(
            identity_id=str(row["identity_id"]),
            password_hash=str(row["password_hash"]),
            principal=self._principal(row),
        )

    async def create_session(
        self,
        session: AuthSession,
        *,
        password_update: PasswordHashUpdate | None,
    ) -> None:
        timestamp = session.created_at.astimezone(UTC).isoformat()
        async with self._runtime(), self._database.create() as unit_of_work:
            await unit_of_work.connection.execute(
                "UPDATE auth_sessions SET state = ? "
                "WHERE user_id = ? AND state = ? AND expires_at <= ?",
                (
                    SessionState.EXPIRED.value,
                    session.user_id,
                    SessionState.ACTIVE.value,
                    timestamp,
                ),
            )
            if password_update is not None:
                await unit_of_work.connection.execute(
                    "UPDATE auth_identities SET password_hash = ?, updated_at = ? "
                    "WHERE id = ? AND password_hash = ?",
                    (
                        password_update.updated_hash,
                        timestamp,
                        password_update.identity_id,
                        password_update.previous_hash,
                    ),
                )
            await unit_of_work.connection.execute(
                "INSERT INTO auth_sessions "
                "(id, user_id, token_hash, csrf_hash, state, expires_at, rotated_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    session.id,
                    session.user_id,
                    session.token_hash,
                    session.csrf_hash,
                    SessionState.ACTIVE.value,
                    session.expires_at.astimezone(UTC).isoformat(),
                    timestamp,
                ),
            )
            await unit_of_work.commit()

    async def get_session(
        self,
        *,
        token_hash: str,
        at: datetime,
    ) -> StoredAuthSession | None:
        now = at.astimezone(UTC)
        async with self._runtime(), self._database.create() as unit_of_work:
            row = await fetch_one(
                unit_of_work.connection,
                "SELECT s.id AS session_id, s.csrf_hash, s.state, s.expires_at, "
                "s.created_at, s.rotated_at, u.id AS user_id, u.display_name, "
                "u.role, u.status FROM auth_sessions s "
                "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (token_hash,),
            )
            if row is None or str(row["state"]) != SessionState.ACTIVE.value:
                await unit_of_work.commit()
                return None
            expires_at = datetime.fromisoformat(str(row["expires_at"])).astimezone(UTC)
            if expires_at <= now:
                await unit_of_work.connection.execute(
                    "UPDATE auth_sessions SET state = ? WHERE id = ? AND state = ?",
                    (
                        SessionState.EXPIRED.value,
                        str(row["session_id"]),
                        SessionState.ACTIVE.value,
                    ),
                )
                await unit_of_work.commit()
                return None
            await unit_of_work.commit()
        rotated_value = row["rotated_at"]
        return StoredAuthSession(
            session_id=str(row["session_id"]),
            csrf_hash=str(row["csrf_hash"]),
            expires_at=expires_at,
            created_at=datetime.fromisoformat(str(row["created_at"])).astimezone(UTC),
            rotated_at=(
                datetime.fromisoformat(str(rotated_value)).astimezone(UTC)
                if rotated_value is not None
                else None
            ),
            principal=self._principal(row),
        )

    async def rotate_session(
        self,
        *,
        session_id: str,
        previous_token_hash: str,
        token_hash: str,
        csrf_hash: str,
        rotated_at: datetime,
    ) -> bool:
        timestamp = rotated_at.astimezone(UTC).isoformat()
        async with self._runtime(), self._database.create() as unit_of_work:
            cursor = await unit_of_work.connection.execute(
                "UPDATE auth_sessions SET token_hash = ?, csrf_hash = ?, rotated_at = ? "
                "WHERE id = ? AND token_hash = ? AND state = ? AND expires_at > ?",
                (
                    token_hash,
                    csrf_hash,
                    timestamp,
                    session_id,
                    previous_token_hash,
                    SessionState.ACTIVE.value,
                    timestamp,
                ),
            )
            try:
                changed = cursor.rowcount == 1
            finally:
                await cursor.close()
            await unit_of_work.commit()
            return changed

    async def revoke_session(
        self,
        *,
        session_id: str,
        token_hash: str,
        revoked_at: datetime,
    ) -> bool:
        del revoked_at
        async with self._runtime(), self._database.create() as unit_of_work:
            cursor = await unit_of_work.connection.execute(
                "UPDATE auth_sessions SET state = ? "
                "WHERE id = ? AND token_hash = ? AND state = ?",
                (
                    SessionState.REVOKED.value,
                    session_id,
                    token_hash,
                    SessionState.ACTIVE.value,
                ),
            )
            try:
                changed = cursor.rowcount == 1
            finally:
                await cursor.close()
            await unit_of_work.commit()
            return changed

    @staticmethod
    def _principal(row: aiosqlite.Row) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            user_id=str(row["user_id"]),
            display_name=str(row["display_name"]),
            role=UserRole(str(row["role"])),
            status=UserStatus(str(row["status"])),
        )
