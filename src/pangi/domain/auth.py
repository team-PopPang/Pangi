"""Framework-free authentication domain values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

_LOCAL_SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{2,79}$")


class UserRole(StrEnum):
    MEMBER = "member"
    SKILL_AUTHOR = "skill_author"
    ADMIN = "admin"
    SYSTEM = "system"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class IdentityProvider(StrEnum):
    LOCAL = "local"
    SLACK = "slack"
    REVERSE_PROXY = "reverse_proxy"


class SessionState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class BootstrapGrant:
    id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LocalAdmin:
    user_id: str
    identity_id: str
    subject: str
    display_name: str
    password_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuthSession:
    id: str
    user_id: str
    token_hash: str
    csrf_hash: str
    expires_at: datetime
    created_at: datetime


def normalize_local_subject(value: str) -> str:
    """Validate and normalize a stable local sign-in identifier."""

    subject = value.strip().casefold()
    if _LOCAL_SUBJECT.fullmatch(subject) is None:
        raise ValueError(
            "local identifier must be 3-80 characters using letters, numbers, or ._@+-"
        )
    return subject


def validate_display_name(value: str) -> str:
    """Validate a human-facing name without accepting blank content."""

    display_name = value.strip()
    if not 1 <= len(display_name) <= 80:
        raise ValueError("display name must be 1-80 characters")
    return display_name


def validate_local_password(value: str) -> str:
    """Apply the minimum local bootstrap password policy."""

    if not 12 <= len(value) <= 256:
        raise ValueError("password must be 12-256 characters")
    return value
