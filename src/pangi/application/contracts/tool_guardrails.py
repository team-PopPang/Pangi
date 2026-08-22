"""Secret-safe contracts for deterministic Tool authorization and execution."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pangi.domain.auth import UserRole
from pangi.domain.guardrails import TrustLevel
from pangi.domain.tool_guardrails import (
    ToolApprovalRequirement,
    ToolConnectionScope,
    ToolExecutionErrorCode,
    ToolGuardrailErrorCode,
    ToolGuardrailOutcome,
    ToolGuardrailStage,
    ToolPermission,
    ToolPolicyEffect,
)

_POLICY_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _validate_opaque_identifier(value: str, *, field_name: str, limit: int = 255) -> None:
    if not 1 <= len(value) <= limit or value.strip() != value:
        raise ValueError(f"{field_name} must be a bounded opaque identifier")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in value):
        raise ValueError(f"{field_name} must contain visible ASCII characters")


def _validate_fingerprint(value: str, *, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ResolvedTool:
    """A Registry snapshot bound to an exact physical Tool and Schema."""

    tool_id: str
    connection_id: str = field(repr=False)
    tool_name: str = field(repr=False)
    connection_scope: ToolConnectionScope
    permission: ToolPermission
    schema_fingerprint: str
    active: bool = True
    connection_owner_user_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _validate_opaque_identifier(self.tool_id, field_name="tool_id")
        _validate_opaque_identifier(self.connection_id, field_name="connection_id")
        _validate_opaque_identifier(self.tool_name, field_name="tool_name")
        try:
            object.__setattr__(
                self,
                "connection_scope",
                ToolConnectionScope(self.connection_scope),
            )
            object.__setattr__(self, "permission", ToolPermission(self.permission))
        except ValueError as error:
            raise ValueError("resolved Tool contains an invalid enum value") from error
        _validate_fingerprint(self.schema_fingerprint, field_name="schema_fingerprint")
        if self.connection_scope is ToolConnectionScope.USER:
            if self.connection_owner_user_id is None:
                raise ValueError("a user-scoped Tool requires a connection owner")
            _validate_opaque_identifier(
                self.connection_owner_user_id,
                field_name="connection_owner_user_id",
            )
        elif self.connection_owner_user_id is not None:
            raise ValueError("an instance-scoped Tool cannot have a user owner")


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """An exact, versioned policy with no implicit organization defaults."""

    policy_version: str
    tool_id: str
    connection_id: str = field(repr=False)
    effect: ToolPolicyEffect
    permission: ToolPermission
    approval: ToolApprovalRequirement
    schema_fingerprint: str
    max_calls_per_run: int
    max_argument_bytes: int
    timeout_seconds: int
    max_result_bytes: int

    def __post_init__(self) -> None:
        if _POLICY_IDENTIFIER.fullmatch(self.policy_version) is None:
            raise ValueError("policy_version must be a stable identifier")
        _validate_opaque_identifier(self.tool_id, field_name="tool_id")
        _validate_opaque_identifier(self.connection_id, field_name="connection_id")
        try:
            object.__setattr__(self, "effect", ToolPolicyEffect(self.effect))
            object.__setattr__(self, "permission", ToolPermission(self.permission))
            object.__setattr__(
                self,
                "approval",
                ToolApprovalRequirement(self.approval),
            )
        except ValueError as error:
            raise ValueError("Tool policy contains an invalid enum value") from error
        _validate_fingerprint(self.schema_fingerprint, field_name="schema_fingerprint")
        if self.max_calls_per_run < 0:
            raise ValueError("max_calls_per_run cannot be negative")
        if self.max_argument_bytes < 1:
            raise ValueError("max_argument_bytes must be positive")
        if not 1 <= self.timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        if self.max_result_bytes < 1:
            raise ValueError("max_result_bytes must be positive")

    @property
    def fingerprint(self) -> str:
        payload = {
            "approval": self.approval.value,
            "connection_id": self.connection_id,
            "effect": self.effect.value,
            "max_argument_bytes": self.max_argument_bytes,
            "max_calls_per_run": self.max_calls_per_run,
            "max_result_bytes": self.max_result_bytes,
            "permission": self.permission.value,
            "policy_version": self.policy_version,
            "schema_fingerprint": self.schema_fingerprint,
            "timeout_seconds": self.timeout_seconds,
            "tool_id": self.tool_id,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    run_id: str = field(repr=False)
    principal_user_id: str = field(repr=False)
    tool_id: str
    arguments: Mapping[str, object] = field(repr=False)
    approval_reference: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _validate_opaque_identifier(self.run_id, field_name="run_id")
        _validate_opaque_identifier(self.principal_user_id, field_name="principal_user_id")
        _validate_opaque_identifier(self.tool_id, field_name="tool_id")
        if not isinstance(self.arguments, Mapping):
            raise ValueError("arguments must be a Mapping")
        if self.approval_reference is not None:
            _validate_opaque_identifier(
                self.approval_reference,
                field_name="approval_reference",
                limit=1_024,
            )


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    """Verified approval claims; source reference and identities stay out of repr."""

    grant_id: str
    subject_user_id: str = field(repr=False)
    approver_user_id: str = field(repr=False)
    approver_role: UserRole
    approval_requirement: ToolApprovalRequirement
    run_id: str = field(repr=False)
    tool_id: str
    arguments_fingerprint: str
    policy_fingerprint: str
    expires_at: datetime

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.grant_id, "grant_id"),
            (self.subject_user_id, "subject_user_id"),
            (self.approver_user_id, "approver_user_id"),
            (self.run_id, "run_id"),
            (self.tool_id, "tool_id"),
        ):
            _validate_opaque_identifier(value, field_name=field_name)
        try:
            object.__setattr__(self, "approver_role", UserRole(self.approver_role))
            object.__setattr__(
                self,
                "approval_requirement",
                ToolApprovalRequirement(self.approval_requirement),
            )
        except ValueError as error:
            raise ValueError("Approval Grant contains an invalid enum value") from error
        if self.approval_requirement is ToolApprovalRequirement.NONE:
            raise ValueError("an Approval Grant must require user or admin approval")
        _validate_fingerprint(
            self.arguments_fingerprint,
            field_name="arguments_fingerprint",
        )
        _validate_fingerprint(self.policy_fingerprint, field_name="policy_fingerprint")
        object.__setattr__(self, "expires_at", _utc(self.expires_at, field_name="expires_at"))


@dataclass(frozen=True, slots=True)
class ToolBudgetReservation:
    allowed: bool
    calls_used: int
    rejection_code: ToolGuardrailErrorCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise ValueError("allowed must be a boolean")
        if self.calls_used < 0:
            raise ValueError("calls_used cannot be negative")
        if self.allowed and self.calls_used < 1:
            raise ValueError("an allowed reservation must consume one call")
        if self.rejection_code is not None:
            try:
                object.__setattr__(
                    self,
                    "rejection_code",
                    ToolGuardrailErrorCode(self.rejection_code),
                )
            except ValueError as error:
                raise ValueError("budget rejection_code is invalid") from error
        if self.allowed and self.rejection_code is not None:
            raise ValueError("an allowed reservation cannot contain a rejection code")


@dataclass(frozen=True, slots=True)
class ToolExecutionLimits:
    timeout_seconds: int
    max_result_bytes: int

    def __post_init__(self) -> None:
        if not 1 <= self.timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        if self.max_result_bytes < 1:
            raise ValueError("max_result_bytes must be positive")


@dataclass(frozen=True, slots=True)
class ToolGuardrailDecision:
    tool_id: str
    stage: ToolGuardrailStage
    outcome: ToolGuardrailOutcome
    policy_version: str | None = None
    policy_fingerprint: str | None = None
    permission: ToolPermission | None = None
    argument_bytes: int | None = None
    calls_used: int | None = None
    error_code: ToolGuardrailErrorCode | None = None
    trust_level: TrustLevel = field(init=False, default=TrustLevel.UNTRUSTED)

    def __post_init__(self) -> None:
        _validate_opaque_identifier(self.tool_id, field_name="tool_id")
        try:
            object.__setattr__(self, "stage", ToolGuardrailStage(self.stage))
            object.__setattr__(self, "outcome", ToolGuardrailOutcome(self.outcome))
            if self.permission is not None:
                object.__setattr__(self, "permission", ToolPermission(self.permission))
            if self.error_code is not None:
                object.__setattr__(
                    self,
                    "error_code",
                    ToolGuardrailErrorCode(self.error_code),
                )
        except ValueError as error:
            raise ValueError("Tool guardrail decision contains an invalid enum value") from error
        if (self.policy_version is None) is not (self.policy_fingerprint is None):
            raise ValueError("policy version and fingerprint must be present together")
        if self.policy_version is not None:
            if _POLICY_IDENTIFIER.fullmatch(self.policy_version) is None:
                raise ValueError("policy_version must be a stable identifier")
            assert self.policy_fingerprint is not None
            _validate_fingerprint(self.policy_fingerprint, field_name="policy_fingerprint")
        if self.argument_bytes is not None and self.argument_bytes < 0:
            raise ValueError("argument_bytes cannot be negative")
        if self.calls_used is not None and self.calls_used < 0:
            raise ValueError("calls_used cannot be negative")
        if self.outcome is ToolGuardrailOutcome.ALLOWED:
            if self.stage is not ToolGuardrailStage.COMPLETE:
                raise ValueError("an allowed Tool decision must be complete")
            if self.error_code is not None or self.policy_version is None:
                raise ValueError("an allowed Tool decision requires policy metadata only")
            if (
                self.permission is None
                or self.argument_bytes is None
                or self.calls_used is None
                or self.calls_used < 1
            ):
                raise ValueError("an allowed Tool decision requires execution metadata")
        elif self.stage is ToolGuardrailStage.COMPLETE or self.error_code is None:
            raise ValueError("a blocked Tool decision requires a rejection stage and error code")

    def as_dict(self) -> dict[str, object]:
        return {
            "tool_id": self.tool_id,
            "trust_level": self.trust_level.value,
            "stage": self.stage.value,
            "outcome": self.outcome.value,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "permission": self.permission.value if self.permission is not None else None,
            "argument_bytes": self.argument_bytes,
            "calls_used": self.calls_used,
            "error_code": self.error_code.value if self.error_code is not None else None,
        }


@dataclass(frozen=True, slots=True)
class GuardedToolCall:
    """A resolved call whose mutable input can no longer change after approval."""

    run_id: str = field(repr=False)
    tool: ResolvedTool = field(repr=False)
    canonical_arguments_json: str = field(repr=False)
    arguments_fingerprint: str
    policy_fingerprint: str
    approval_grant_id: str | None
    limits: ToolExecutionLimits
    decision: ToolGuardrailDecision

    def __post_init__(self) -> None:
        _validate_opaque_identifier(self.run_id, field_name="run_id")
        _validate_fingerprint(
            self.arguments_fingerprint,
            field_name="arguments_fingerprint",
        )
        _validate_fingerprint(self.policy_fingerprint, field_name="policy_fingerprint")
        if self.approval_grant_id is not None:
            _validate_opaque_identifier(
                self.approval_grant_id,
                field_name="approval_grant_id",
            )
        if self.decision.outcome is not ToolGuardrailOutcome.ALLOWED:
            raise ValueError("a guarded Tool call requires an allowed decision")
        if self.decision.tool_id != self.tool.tool_id:
            raise ValueError("guarded Tool and decision identifiers must match")
        if self.decision.policy_fingerprint != self.policy_fingerprint:
            raise ValueError("guarded Tool and decision policies must match")
        encoded = self.canonical_arguments_json.encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != self.arguments_fingerprint:
            raise ValueError("arguments_fingerprint must match canonical arguments")
        try:
            arguments = json.loads(self.canonical_arguments_json)
        except (json.JSONDecodeError, RecursionError) as error:
            raise ValueError("canonical_arguments_json must contain valid JSON") from error
        if not isinstance(arguments, dict):
            raise ValueError("canonical_arguments_json must contain a JSON object")
        expected = json.dumps(
            arguments,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if expected != self.canonical_arguments_json:
            raise ValueError("canonical_arguments_json must use canonical ordering")
        if self.decision.argument_bytes != len(encoded):
            raise ValueError("decision argument_bytes must match canonical arguments")


@dataclass(frozen=True, slots=True)
class GuardedToolExecution:
    result: object = field(repr=False)
    decision: ToolGuardrailDecision

    def __post_init__(self) -> None:
        if self.decision.outcome is not ToolGuardrailOutcome.ALLOWED:
            raise ValueError("a guarded Tool execution requires an allowed decision")


class ToolGuardrailBlockedError(RuntimeError):
    """A deterministic rejection whose message contains no arguments or approval data."""

    def __init__(self, decision: ToolGuardrailDecision) -> None:
        if decision.error_code is None:
            raise ValueError("a blocked Tool decision requires an error code")
        super().__init__(f"Tool guardrail blocked call: {decision.error_code.value}")
        self.decision = decision

    @property
    def code(self) -> ToolGuardrailErrorCode:
        assert self.decision.error_code is not None
        return self.decision.error_code


class ToolExecutionFailedError(RuntimeError):
    """A secret-safe external Tool failure exposed by the execution boundary."""

    code = ToolExecutionErrorCode.FAILED

    def __init__(self) -> None:
        super().__init__("Tool execution failed")
