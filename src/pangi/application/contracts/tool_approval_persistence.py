"""Secret-safe contracts for issuing and atomically consuming Tool approvals."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pangi.application.contracts.tool_guardrails import ApprovalGrant
from pangi.domain.auth import UserRole
from pangi.domain.tool_guardrails import (
    ToolApprovalConsumptionStatus,
    ToolApprovalRequirement,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
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


def _approval_requirement(value: ToolApprovalRequirement) -> ToolApprovalRequirement:
    try:
        requirement = ToolApprovalRequirement(value)
    except ValueError as error:
        raise ValueError("approval_requirement is invalid") from error
    if requirement is ToolApprovalRequirement.NONE:
        raise ValueError("approval_requirement must be user or admin")
    return requirement


@dataclass(frozen=True, slots=True)
class ToolApprovalIssuancePolicy:
    """Explicit security ceiling for short-lived approval references."""

    max_ttl_seconds: int

    def __post_init__(self) -> None:
        if not 1 <= self.max_ttl_seconds <= 3_600:
            raise ValueError("max_ttl_seconds must be between 1 and 3600")


@dataclass(frozen=True, slots=True)
class ToolApprovalIssueCommand:
    """Trusted command for one approval bound to exact guarded-call claims."""

    subject_user_id: str = field(repr=False)
    approver_user_id: str = field(repr=False)
    approver_role: UserRole
    run_id: str = field(repr=False)
    tool_id: str
    arguments_fingerprint: str
    policy_fingerprint: str
    approval_requirement: ToolApprovalRequirement
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.subject_user_id, "subject_user_id"),
            (self.approver_user_id, "approver_user_id"),
            (self.run_id, "run_id"),
            (self.tool_id, "tool_id"),
        ):
            _identifier(value, field_name=field_name)
        try:
            object.__setattr__(self, "approver_role", UserRole(self.approver_role))
        except ValueError as error:
            raise ValueError("approver_role is invalid") from error
        object.__setattr__(
            self,
            "approval_requirement",
            _approval_requirement(self.approval_requirement),
        )
        _fingerprint(self.arguments_fingerprint, field_name="arguments_fingerprint")
        _fingerprint(self.policy_fingerprint, field_name="policy_fingerprint")
        issued_at = _utc(self.issued_at, field_name="issued_at")
        expires_at = _utc(self.expires_at, field_name="expires_at")
        if expires_at <= issued_at:
            raise ValueError("expires_at must follow issued_at")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class IssuedToolApproval:
    """One raw reference returned only to the trusted caller that requested issuance."""

    reference: str = field(repr=False)
    grant: ApprovalGrant

    def __post_init__(self) -> None:
        if not 32 <= len(self.reference) <= 1_024:
            raise ValueError("reference must be a bounded opaque secret")
        if any(ord(character) < 0x21 or ord(character) > 0x7E for character in self.reference):
            raise ValueError("reference must contain visible ASCII characters")
        if not isinstance(self.grant, ApprovalGrant):
            raise TypeError("grant must be an ApprovalGrant")


@dataclass(frozen=True, slots=True)
class ToolApprovalExpectation:
    """Exact claims that must match before a reference can be consumed."""

    subject_user_id: str = field(repr=False)
    run_id: str = field(repr=False)
    tool_id: str
    arguments_fingerprint: str
    policy_fingerprint: str
    approval_requirement: ToolApprovalRequirement
    consumed_at: datetime

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.subject_user_id, "subject_user_id"),
            (self.run_id, "run_id"),
            (self.tool_id, "tool_id"),
        ):
            _identifier(value, field_name=field_name)
        _fingerprint(self.arguments_fingerprint, field_name="arguments_fingerprint")
        _fingerprint(self.policy_fingerprint, field_name="policy_fingerprint")
        object.__setattr__(
            self,
            "approval_requirement",
            _approval_requirement(self.approval_requirement),
        )
        object.__setattr__(
            self,
            "consumed_at",
            _utc(self.consumed_at, field_name="consumed_at"),
        )


@dataclass(frozen=True, slots=True)
class ToolApprovalConsumption:
    status: ToolApprovalConsumptionStatus
    grant: ApprovalGrant | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self,
                "status",
                ToolApprovalConsumptionStatus(self.status),
            )
        except ValueError as error:
            raise ValueError("Tool Approval consumption status is invalid") from error
        if self.status is ToolApprovalConsumptionStatus.CONSUMED:
            if not isinstance(self.grant, ApprovalGrant):
                raise ValueError("a consumed approval requires its verified Grant")
        elif self.grant is not None:
            raise ValueError("a rejected approval cannot disclose Grant claims")

    @classmethod
    def consumed(cls, grant: ApprovalGrant) -> ToolApprovalConsumption:
        return cls(ToolApprovalConsumptionStatus.CONSUMED, grant)

    @classmethod
    def invalid(cls) -> ToolApprovalConsumption:
        return cls(ToolApprovalConsumptionStatus.INVALID)

    @classmethod
    def expired(cls) -> ToolApprovalConsumption:
        return cls(ToolApprovalConsumptionStatus.EXPIRED)
