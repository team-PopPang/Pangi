"""SQLite persistence for Bootstrap Admin state transitions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.application.contracts.bootstrap import (
    BootstrapAdminResult,
    BootstrapIssueStatus,
)
from pangi.application.ports.bootstrap_admin import (
    BootstrapAlreadyConfiguredError,
    BootstrapIdentityConflictError,
    InvalidBootstrapGrantError,
)
from pangi.domain.auth import BootstrapGrant, IdentityProvider, LocalAdmin, UserRole, UserStatus

_INVALID_GRANT_MESSAGE = "Bootstrap Grant is invalid or unavailable"


class SqliteBootstrapStore:
    """Own Bootstrap writes on the runtime's serialized unit of work."""

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

    async def issue_grant(
        self,
        grant: BootstrapGrant,
        *,
        rotate: bool,
    ) -> BootstrapIssueStatus:
        async with self._runtime(), self._database.create() as unit_of_work:
            admin = await fetch_one(
                unit_of_work.connection,
                "SELECT id FROM users WHERE role = ? LIMIT 1",
                (UserRole.ADMIN.value,),
            )
            if admin is not None:
                await unit_of_work.commit()
                return BootstrapIssueStatus.ADMIN_EXISTS

            existing = await fetch_one(
                unit_of_work.connection,
                "SELECT id FROM bootstrap_grants LIMIT 1",
            )
            if existing is not None and not rotate:
                await unit_of_work.commit()
                return BootstrapIssueStatus.ALREADY_ISSUED

            if rotate:
                await unit_of_work.connection.execute(
                    "UPDATE bootstrap_grants SET revoked_at = ? "
                    "WHERE consumed_at IS NULL AND revoked_at IS NULL",
                    (grant.created_at.astimezone(UTC).isoformat(),),
                )
            await unit_of_work.connection.execute(
                "INSERT INTO bootstrap_grants "
                "(id, token_hash, expires_at, consumed_at, consumed_by_user_id, "
                "revoked_at, created_at) VALUES (?, ?, ?, NULL, NULL, NULL, ?)",
                (
                    grant.id,
                    grant.token_hash,
                    grant.expires_at.astimezone(UTC).isoformat(),
                    grant.created_at.astimezone(UTC).isoformat(),
                ),
            )
            await unit_of_work.commit()
            return BootstrapIssueStatus.ISSUED

    async def create_admin(
        self,
        *,
        token_hash: str,
        admin: LocalAdmin,
    ) -> BootstrapAdminResult:
        now = admin.created_at.astimezone(UTC)
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                grant = await fetch_one(
                    unit_of_work.connection,
                    "SELECT id, expires_at FROM bootstrap_grants "
                    "WHERE token_hash = ? AND consumed_at IS NULL AND revoked_at IS NULL",
                    (token_hash,),
                )
                if grant is None or datetime.fromisoformat(str(grant["expires_at"])) <= now:
                    raise InvalidBootstrapGrantError(_INVALID_GRANT_MESSAGE)

                configured = await fetch_one(
                    unit_of_work.connection,
                    "SELECT id FROM users WHERE role = ? LIMIT 1",
                    (UserRole.ADMIN.value,),
                )
                if configured is not None:
                    raise BootstrapAlreadyConfiguredError("Bootstrap is already configured")

                timestamp = now.isoformat()
                await unit_of_work.connection.execute(
                    "INSERT INTO users "
                    "(id, display_name, role, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        admin.user_id,
                        admin.display_name,
                        UserRole.ADMIN.value,
                        UserStatus.ACTIVE.value,
                        timestamp,
                        timestamp,
                    ),
                )
                await unit_of_work.connection.execute(
                    "INSERT INTO auth_identities "
                    "(id, user_id, provider, subject, password_hash, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        admin.identity_id,
                        admin.user_id,
                        IdentityProvider.LOCAL.value,
                        admin.subject,
                        admin.password_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                cursor = await unit_of_work.connection.execute(
                    "UPDATE bootstrap_grants "
                    "SET consumed_at = ?, consumed_by_user_id = ? "
                    "WHERE id = ? AND consumed_at IS NULL AND revoked_at IS NULL",
                    (timestamp, admin.user_id, str(grant["id"])),
                )
                try:
                    if cursor.rowcount != 1:
                        raise InvalidBootstrapGrantError(_INVALID_GRANT_MESSAGE)
                finally:
                    await cursor.close()
                await unit_of_work.commit()
        except aiosqlite.IntegrityError as error:
            raise BootstrapIdentityConflictError(
                "The requested local identity is unavailable"
            ) from error
        return BootstrapAdminResult(
            user_id=admin.user_id,
            local_id=admin.subject,
            display_name=admin.display_name,
        )

    async def validate_grant(self, *, token_hash: str, at: datetime) -> None:
        """Perform a cheap preflight before the service computes Argon2id."""

        now = at.astimezone(UTC)
        async with self._runtime(), self._database.create() as unit_of_work:
            grant = await fetch_one(
                unit_of_work.connection,
                "SELECT expires_at FROM bootstrap_grants "
                "WHERE token_hash = ? AND consumed_at IS NULL AND revoked_at IS NULL",
                (token_hash,),
            )
            if grant is None or datetime.fromisoformat(str(grant["expires_at"])) <= now:
                raise InvalidBootstrapGrantError(_INVALID_GRANT_MESSAGE)
            await unit_of_work.commit()
