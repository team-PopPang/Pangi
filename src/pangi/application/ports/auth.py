"""Authentication ports shared by Web and outbound adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pangi.application.contracts.auth import (
    IssuedSession,
    LocalIdentityRecord,
    PasswordHashUpdate,
    SessionView,
    StoredAuthSession,
)
from pangi.domain.auth import AuthSession


class AuthenticationError(RuntimeError):
    """Base class for expected, secret-safe authentication failures."""


class InvalidCredentialsError(AuthenticationError):
    """The submitted local credentials cannot be authenticated."""


class AuthenticationRequiredError(AuthenticationError):
    """A valid active Session is required."""


class CsrfRejectedError(AuthenticationError):
    """The request is not bound to the authenticated browser Session."""


class PermissionDeniedError(AuthenticationError):
    """The authenticated Principal does not hold an allowed role."""


class LoginRateLimitedError(AuthenticationError):
    """Too many failed login attempts occurred in the active window."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many login attempts")
        self.retry_after_seconds = retry_after_seconds


class AuthSessionPort(Protocol):
    async def login(
        self,
        *,
        local_id: str,
        password: str,
        source: str,
    ) -> IssuedSession:
        """Authenticate a Local Identity and create a persistent Session."""

        ...

    async def current_session(self, *, session_token: str) -> SessionView:
        """Return the active Session without exposing its bearer values."""

        ...

    async def rotate(
        self,
        *,
        session_token: str,
        csrf_token: str,
    ) -> IssuedSession:
        """Replace both browser bearer values while preserving absolute expiry."""

        ...

    async def logout(self, *, session_token: str, csrf_token: str) -> None:
        """Revoke one active Session."""

        ...


class AuthSessionStore(Protocol):
    async def find_local_identity(self, *, subject: str) -> LocalIdentityRecord | None:
        ...

    async def create_session(
        self,
        session: AuthSession,
        *,
        password_update: PasswordHashUpdate | None,
    ) -> None:
        ...

    async def get_session(
        self,
        *,
        token_hash: str,
        at: datetime,
    ) -> StoredAuthSession | None:
        ...

    async def rotate_session(
        self,
        *,
        session_id: str,
        previous_token_hash: str,
        token_hash: str,
        csrf_hash: str,
        rotated_at: datetime,
    ) -> bool:
        ...

    async def revoke_session(
        self,
        *,
        session_id: str,
        token_hash: str,
        revoked_at: datetime,
    ) -> bool:
        ...


class PasswordVerifier(Protocol):
    def hash(self, password: str) -> str:
        ...

    def verify(self, password_hash: str, password: str) -> bool:
        ...

    def needs_rehash(self, password_hash: str) -> bool:
        ...


class LoginAttemptLimiter(Protocol):
    def reserve(self, key: str, *, at: datetime) -> int | None:
        """Reserve one verification attempt or return the retry delay."""

        ...

    def clear(self, key: str) -> None:
        ...
