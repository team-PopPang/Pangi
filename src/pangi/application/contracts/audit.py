"""Secret-safe Audit write, query, and pagination contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from pangi.domain.audit import AuditEvent, AuditOutcome

_POLICY_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AuditRedactionErrorCode(StrEnum):
    INPUT_TOO_DEEP = "audit_input_too_deep"
    INPUT_TOO_LARGE = "audit_input_too_large"
    INPUT_CYCLE = "audit_input_cycle"
    INPUT_INVALID = "audit_input_invalid"
    REDACTION_FAILED = "audit_redaction_failed"


class AuditRedactionError(RuntimeError):
    """A raw Audit payload could not become a bounded safe event."""

    def __init__(self, code: AuditRedactionErrorCode) -> None:
        super().__init__(f"Audit payload rejected: {code.value}")
        self.code = code


@dataclass(frozen=True, slots=True)
class AuditPolicy:
    """Versioned limits and retention used by every Audit write boundary."""

    policy_version: str
    max_metadata_bytes: int
    max_depth: int
    max_collection_items: int
    retention_days: int

    def __post_init__(self) -> None:
        if _POLICY_IDENTIFIER.fullmatch(self.policy_version) is None:
            raise ValueError("audit policy_version must be a stable identifier")
        if not 1 <= self.max_metadata_bytes <= 16 * 1024 * 1024:
            raise ValueError("max_metadata_bytes must be between 1 byte and 16 MiB")
        if not 1 <= self.max_depth <= 100:
            raise ValueError("max_depth must be between 1 and 100")
        if not 1 <= self.max_collection_items <= 100_000:
            raise ValueError("max_collection_items must be between 1 and 100000")
        if not 1 <= self.retention_days <= 3650:
            raise ValueError("retention_days must be between 1 and 3650")

    @property
    def fingerprint(self) -> str:
        payload = {
            "max_collection_items": self.max_collection_items,
            "max_depth": self.max_depth,
            "max_metadata_bytes": self.max_metadata_bytes,
            "policy_version": self.policy_version,
            "retention_days": self.retention_days,
        }
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditEventDraft:
    """Potentially unsafe management metadata accepted only by the final writer."""

    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    outcome: AuditOutcome
    created_at: datetime
    before_summary: Mapping[str, object] | None = field(default=None, repr=False)
    after_summary: Mapping[str, object] | None = field(default=None, repr=False)
    details: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "outcome", AuditOutcome(self.outcome))
        except ValueError as error:
            raise ValueError("audit draft outcome is invalid") from error
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("audit draft created_at must be timezone-aware")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        for value, field_name in (
            (self.before_summary, "before_summary"),
            (self.after_summary, "after_summary"),
            (self.details, "details"),
        ):
            if value is not None and not isinstance(value, Mapping):
                raise ValueError(f"{field_name} must be a mapping or None")


@dataclass(frozen=True, slots=True)
class AuditListQuery:
    """Caller-controlled exact filters without an authorization scope."""

    actor_id: str | None = None
    actions: tuple[str, ...] = ()
    resource_type: str | None = None
    resource_id: str | None = None
    outcomes: tuple[AuditOutcome, ...] = ()
    created_from: datetime | None = None
    created_to: datetime | None = None
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.actions, tuple) or not isinstance(self.outcomes, tuple):
            raise ValueError("audit list filters must be immutable tuples")
        try:
            object.__setattr__(
                self,
                "outcomes",
                tuple(AuditOutcome(outcome) for outcome in self.outcomes),
            )
        except ValueError as error:
            raise ValueError("audit outcome filter is invalid") from error
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("audit action filters cannot contain duplicates")
        if len(set(self.outcomes)) != len(self.outcomes):
            raise ValueError("audit outcome filters cannot contain duplicates")
        if not 1 <= self.limit <= 100:
            raise ValueError("audit list limit must be between 1 and 100")
        if self.cursor is not None and not 1 <= len(self.cursor) <= 1024:
            raise ValueError("audit list cursor must be between 1 and 1024 characters")
        for field_name in ("created_from", "created_to"):
            value = getattr(self, field_name)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError(f"{field_name} must be timezone-aware")
                object.__setattr__(self, field_name, value.astimezone(UTC))
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from > self.created_to
        ):
            raise ValueError("audit created_from cannot be later than created_to")


@dataclass(frozen=True, slots=True)
class AuditCursorPosition:
    created_at: datetime
    event_id: str


@dataclass(frozen=True, slots=True)
class AuditStoreQuery:
    actor_id: str | None
    actions: tuple[str, ...]
    resource_type: str | None
    resource_id: str | None
    outcomes: tuple[AuditOutcome, ...]
    created_from: datetime | None
    created_to: datetime | None
    limit: int
    after: AuditCursorPosition | None


@dataclass(frozen=True, slots=True)
class AuditListPage:
    items: tuple[AuditEvent, ...]
    next_cursor: str | None


def validate_sha256(value: str, *, field_name: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
