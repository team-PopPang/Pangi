"""Secret-safe contracts for governed Tool Invocation persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from pangi.application.contracts.tool_guardrails import (
    GuardedToolCall,
    ToolGuardrailDecision,
)
from pangi.domain.tool_guardrails import (
    ToolExecutionErrorCode,
    ToolGuardrailOutcome,
    ToolInvocationState,
    ToolPermission,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _identifier(value: str, *, field_name: str, limit: int = 120) -> None:
    if not 1 <= len(value) <= limit or value.strip() != value:
        raise ValueError(f"{field_name} must be a bounded opaque identifier")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in value):
        raise ValueError(f"{field_name} must contain visible ASCII characters")


def _run_identifier(value: str, *, field_name: str) -> None:
    if not 16 <= len(value) <= 64 or value.strip() != value:
        raise ValueError(f"{field_name} must contain 16-64 non-padding characters")


def _fingerprint(value: str, *, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ToolInvocationContext:
    """Run and optional Step that own exactly one Tool attempt."""

    run_id: str
    step_id: str | None = None

    def __post_init__(self) -> None:
        _run_identifier(self.run_id, field_name="run_id")
        if self.step_id is not None:
            _run_identifier(self.step_id, field_name="step_id")


@dataclass(frozen=True, slots=True)
class ToolInvocationStart:
    """Safe metadata committed before the external Tool call starts."""

    invocation_id: str
    context: ToolInvocationContext
    connection_id: str
    stable_tool_id: str
    policy_version: str
    policy_fingerprint: str
    approval_grant_id: str | None
    arguments_fingerprint: str
    argument_bytes: int
    permission: ToolPermission
    calls_used: int
    timeout_seconds: int
    max_result_bytes: int
    started_at: datetime

    def __post_init__(self) -> None:
        _run_identifier(self.invocation_id, field_name="invocation_id")
        if not isinstance(self.context, ToolInvocationContext):
            raise TypeError("context must be a ToolInvocationContext")
        for value, field_name, limit in (
            (self.connection_id, "connection_id", 120),
            (self.stable_tool_id, "stable_tool_id", 120),
            (self.policy_version, "policy_version", 120),
        ):
            _identifier(value, field_name=field_name, limit=limit)
        if self.approval_grant_id is not None:
            _identifier(
                self.approval_grant_id,
                field_name="approval_grant_id",
                limit=64,
            )
        _fingerprint(self.policy_fingerprint, field_name="policy_fingerprint")
        _fingerprint(self.arguments_fingerprint, field_name="arguments_fingerprint")
        try:
            object.__setattr__(self, "permission", ToolPermission(self.permission))
        except ValueError as error:
            raise ValueError("permission is invalid") from error
        if self.argument_bytes < 0:
            raise ValueError("argument_bytes must be non-negative")
        if self.calls_used < 1:
            raise ValueError("calls_used must identify a consumed Tool budget attempt")
        if not 1 <= self.timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        if self.max_result_bytes < 1:
            raise ValueError("max_result_bytes must be positive")
        object.__setattr__(
            self,
            "started_at",
            _utc(self.started_at, field_name="started_at"),
        )

    @classmethod
    def from_guarded_call(
        cls,
        invocation_id: str,
        context: ToolInvocationContext,
        call: GuardedToolCall,
        *,
        started_at: datetime,
    ) -> ToolInvocationStart:
        decision = call.decision
        if decision.policy_version is None or decision.calls_used is None:
            raise ValueError("an allowed Tool decision requires persistence metadata")
        if decision.permission is None or decision.argument_bytes is None:
            raise ValueError("an allowed Tool decision requires execution metadata")
        return cls(
            invocation_id=invocation_id,
            context=context,
            connection_id=call.tool.connection_id,
            stable_tool_id=call.tool.tool_id,
            policy_version=decision.policy_version,
            policy_fingerprint=call.policy_fingerprint,
            approval_grant_id=call.approval_grant_id,
            arguments_fingerprint=call.arguments_fingerprint,
            argument_bytes=decision.argument_bytes,
            permission=decision.permission,
            calls_used=decision.calls_used,
            timeout_seconds=call.limits.timeout_seconds,
            max_result_bytes=call.limits.max_result_bytes,
            started_at=started_at,
        )


@dataclass(frozen=True, slots=True)
class ToolInvocationDenial:
    """Safe metadata for an attempt stopped before external execution."""

    invocation_id: str
    context: ToolInvocationContext
    decision: ToolGuardrailDecision
    denied_at: datetime

    def __post_init__(self) -> None:
        _run_identifier(self.invocation_id, field_name="invocation_id")
        if not isinstance(self.context, ToolInvocationContext):
            raise TypeError("context must be a ToolInvocationContext")
        if self.decision.outcome is not ToolGuardrailOutcome.BLOCKED:
            raise ValueError("a denied Tool Invocation requires a blocked decision")
        object.__setattr__(
            self,
            "denied_at",
            _utc(self.denied_at, field_name="denied_at"),
        )


@dataclass(frozen=True, slots=True)
class ToolInvocationFinish:
    """Terminal metadata without raw Tool result or exception content."""

    invocation_id: str
    state: ToolInvocationState
    duration_ms: int
    finished_at: datetime
    error_code: ToolExecutionErrorCode | None = None

    def __post_init__(self) -> None:
        _run_identifier(self.invocation_id, field_name="invocation_id")
        try:
            object.__setattr__(self, "state", ToolInvocationState(self.state))
            if self.error_code is not None:
                object.__setattr__(
                    self,
                    "error_code",
                    ToolExecutionErrorCode(self.error_code),
                )
        except ValueError as error:
            raise ValueError("Tool Invocation terminal metadata is invalid") from error
        if self.state not in {
            ToolInvocationState.COMPLETED,
            ToolInvocationState.FAILED,
            ToolInvocationState.CANCELLED,
        }:
            raise ValueError("Tool Invocation finish must be terminal")
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        if self.state is ToolInvocationState.COMPLETED:
            if self.error_code is not None:
                raise ValueError("a completed Tool Invocation cannot have an error code")
        elif self.error_code is None:
            raise ValueError("a failed or cancelled Tool Invocation requires an error code")
        object.__setattr__(
            self,
            "finished_at",
            _utc(self.finished_at, field_name="finished_at"),
        )

    @classmethod
    def completed(
        cls,
        invocation_id: str,
        *,
        duration_ms: int,
        finished_at: datetime,
    ) -> ToolInvocationFinish:
        return cls(
            invocation_id,
            ToolInvocationState.COMPLETED,
            duration_ms,
            finished_at,
        )

    @classmethod
    def failed(
        cls,
        invocation_id: str,
        *,
        duration_ms: int,
        finished_at: datetime,
    ) -> ToolInvocationFinish:
        return cls(
            invocation_id,
            ToolInvocationState.FAILED,
            duration_ms,
            finished_at,
            ToolExecutionErrorCode.FAILED,
        )

    @classmethod
    def cancelled(
        cls,
        invocation_id: str,
        *,
        duration_ms: int,
        finished_at: datetime,
    ) -> ToolInvocationFinish:
        return cls(
            invocation_id,
            ToolInvocationState.CANCELLED,
            duration_ms,
            finished_at,
            ToolExecutionErrorCode.CANCELLED,
        )
