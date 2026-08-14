"""Authentication ports shared by CLI, Web, and persistence adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pangi.application.contracts.bootstrap import (
    BootstrapAdminResult,
    BootstrapIssueResult,
    BootstrapIssueStatus,
)
from pangi.domain.auth import BootstrapGrant, LocalAdmin


class BootstrapOperationError(RuntimeError):
    """Base class for expected, secret-safe Bootstrap failures."""


class InvalidBootstrapGrantError(BootstrapOperationError):
    """The submitted Grant cannot be used."""


class BootstrapAlreadyConfiguredError(BootstrapOperationError):
    """An Admin already exists, so Bootstrap is permanently closed."""


class BootstrapIdentityConflictError(BootstrapOperationError):
    """The requested local identity already exists."""


class BootstrapAdminPort(Protocol):
    async def issue_url(self, *, rotate: bool = False) -> BootstrapIssueResult:
        """Issue a one-time URL without storing or logging its raw token."""

        ...

    async def create_admin(
        self,
        *,
        token: str,
        local_id: str,
        display_name: str,
        password: str,
    ) -> BootstrapAdminResult:
        """Consume one Grant while atomically creating the first Admin."""

        ...


class BootstrapStore(Protocol):
    async def issue_grant(
        self,
        grant: BootstrapGrant,
        *,
        rotate: bool,
    ) -> BootstrapIssueStatus:
        """Persist a new Grant if Bootstrap state permits it."""

        ...

    async def validate_grant(self, *, token_hash: str, at: datetime) -> None:
        """Reject an unusable Grant before expensive password hashing."""

        ...

    async def create_admin(
        self,
        *,
        token_hash: str,
        admin: LocalAdmin,
    ) -> BootstrapAdminResult:
        """Validate, consume, and create inside one transaction."""

        ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str:
        """Return an encoded one-way password hash."""

        ...
