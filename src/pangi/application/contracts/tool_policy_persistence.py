"""Secret-safe immutable Tool Policy persistence contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from pangi.application.contracts.tool_guardrails import ToolPolicy
from pangi.domain.tool_guardrails import ToolPolicyState

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_POLICY_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _identifier(value: str, *, field_name: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a stable identifier")


def _fingerprint(value: str, *, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ToolPolicyVersion:
    """One immutable Tool Policy version and its activation state."""

    policy: ToolPolicy
    state: ToolPolicyState
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.policy, ToolPolicy):
            raise TypeError("policy must be a ToolPolicy")
        try:
            object.__setattr__(self, "state", ToolPolicyState(self.state))
        except ValueError as error:
            raise ValueError("Tool Policy state is invalid") from error
        created_at = _utc(self.created_at, field_name="created_at")
        updated_at = _utc(self.updated_at, field_name="updated_at")
        if updated_at < created_at:
            raise ValueError("updated_at cannot precede created_at")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)

    @property
    def fingerprint(self) -> str:
        return self.policy.fingerprint


@dataclass(frozen=True, slots=True)
class ToolPolicyActivationCommand:
    """CAS command for activating one reviewed Tool Policy version."""

    actor_id: str
    tool_id: str
    policy_version: str
    candidate_fingerprint: str
    expected_active_fingerprint: str | None
    activated_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.actor_id, field_name="actor_id")
        _identifier(self.tool_id, field_name="tool_id")
        if _POLICY_VERSION.fullmatch(self.policy_version) is None:
            raise ValueError("policy_version must be a stable identifier")
        _fingerprint(self.candidate_fingerprint, field_name="candidate_fingerprint")
        if self.expected_active_fingerprint is not None:
            _fingerprint(
                self.expected_active_fingerprint,
                field_name="expected_active_fingerprint",
            )
        object.__setattr__(
            self,
            "activated_at",
            _utc(self.activated_at, field_name="activated_at"),
        )


@dataclass(frozen=True, slots=True)
class ToolPolicyActivation:
    """Activated Policy plus the exact baseline it replaced."""

    version: ToolPolicyVersion
    previous_active_fingerprint: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.version, ToolPolicyVersion):
            raise TypeError("version must be a ToolPolicyVersion")
        if self.version.state is not ToolPolicyState.ACTIVE:
            raise ValueError("an activation result requires an active Tool Policy")
        if self.previous_active_fingerprint is not None:
            _fingerprint(
                self.previous_active_fingerprint,
                field_name="previous_active_fingerprint",
            )
