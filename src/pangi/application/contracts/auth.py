"""Secret-safe authentication request and result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pangi.domain.auth import UserRole, UserStatus


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    user_id: str
    display_name: str
    role: UserRole
    status: UserStatus

    def as_dict(self) -> dict[str, str]:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "role": self.role.value,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class LocalIdentityRecord:
    identity_id: str
    password_hash: str
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class StoredAuthSession:
    session_id: str
    csrf_hash: str
    expires_at: datetime
    created_at: datetime
    rotated_at: datetime | None
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class SessionView:
    principal: AuthenticatedPrincipal
    expires_at: datetime
    rotation_due_at: datetime
    rotation_due: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "principal": self.principal.as_dict(),
            "expires_at": self.expires_at.isoformat(),
            "rotation_due_at": self.rotation_due_at.isoformat(),
            "rotation_due": self.rotation_due,
        }


@dataclass(frozen=True, slots=True)
class IssuedSession:
    session_token: str
    csrf_token: str
    view: SessionView


@dataclass(frozen=True, slots=True)
class PasswordHashUpdate:
    identity_id: str
    previous_hash: str
    updated_hash: str
