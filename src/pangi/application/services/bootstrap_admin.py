"""Bootstrap Admin issuance and consumption use case."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pangi.application.contracts.bootstrap import (
    BootstrapAdminResult,
    BootstrapIssueResult,
    BootstrapIssueStatus,
)
from pangi.application.ports.bootstrap_admin import (
    BootstrapAdminPort,
    BootstrapStore,
    InvalidBootstrapGrantError,
    PasswordHasher,
)
from pangi.domain.auth import (
    BootstrapGrant,
    LocalAdmin,
    normalize_local_subject,
    validate_display_name,
    validate_local_password,
)

Clock = Callable[[], datetime]
SecretFactory = Callable[[], str]
IdFactory = Callable[[], str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _secret() -> str:
    return secrets.token_urlsafe(32)


def _identifier() -> str:
    return uuid.uuid4().hex


def hash_token(token: str) -> str:
    """Hash a raw bearer value before it crosses the persistence port."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class BootstrapAdminService(BootstrapAdminPort):
    def __init__(
        self,
        store: BootstrapStore,
        password_hasher: PasswordHasher,
        *,
        public_base_url: str,
        grant_ttl_minutes: int,
        clock: Clock = _utc_now,
        secret_factory: SecretFactory = _secret,
        id_factory: IdFactory = _identifier,
    ) -> None:
        self._store = store
        self._password_hasher = password_hasher
        self._public_base_url = public_base_url.rstrip("/")
        self._grant_ttl = timedelta(minutes=grant_ttl_minutes)
        self._clock = clock
        self._secret_factory = secret_factory
        self._id_factory = id_factory

    async def issue_url(self, *, rotate: bool = False) -> BootstrapIssueResult:
        now = self._clock().astimezone(UTC)
        raw_token = self._secret_factory()
        expires_at = now + self._grant_ttl
        grant = BootstrapGrant(
            id=self._id_factory(),
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
            created_at=now,
        )
        status = await self._store.issue_grant(grant, rotate=rotate)
        if status is not BootstrapIssueStatus.ISSUED:
            return BootstrapIssueResult(status)
        return BootstrapIssueResult(
            status,
            bootstrap_url=f"{self._public_base_url}/bootstrap#{raw_token}",
            expires_at=expires_at,
        )

    async def create_admin(
        self,
        *,
        token: str,
        local_id: str,
        display_name: str,
        password: str,
    ) -> BootstrapAdminResult:
        if not 20 <= len(token) <= 256:
            raise InvalidBootstrapGrantError("Bootstrap Grant is invalid or unavailable")
        subject = normalize_local_subject(local_id)
        safe_name = validate_display_name(display_name)
        safe_password = validate_local_password(password)
        now = self._clock().astimezone(UTC)
        token_hash = hash_token(token)
        await self._store.validate_grant(token_hash=token_hash, at=now)
        admin = LocalAdmin(
            user_id=self._id_factory(),
            identity_id=self._id_factory(),
            subject=subject,
            display_name=safe_name,
            password_hash=self._password_hasher.hash(safe_password),
            created_at=now,
        )
        return await self._store.create_admin(token_hash=token_hash, admin=admin)
