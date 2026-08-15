"""Ports used by the framework-free Tool guardrail application service."""

from __future__ import annotations

from typing import Protocol

from pangi.application.contracts.tool_guardrails import (
    ApprovalGrant,
    GuardedToolCall,
    ResolvedTool,
    ToolBudgetReservation,
    ToolPolicy,
)


class StableToolResolver(Protocol):
    async def resolve(self, tool_id: str) -> ResolvedTool | None:
        """Resolve an opaque stable ID to one exact Registry snapshot."""

        ...


class ToolPolicyProvider(Protocol):
    async def get_policy(self, *, tool_id: str, connection_id: str) -> ToolPolicy | None:
        """Return one exact policy or None so the Engine can default-deny."""

        ...


class ToolArgumentValidator(Protocol):
    async def validate_arguments(
        self,
        *,
        tool: ResolvedTool,
        canonical_arguments_json: str,
    ) -> bool:
        """Validate canonical arguments against the resolved Schema snapshot."""

        ...


class ToolApprovalVerifier(Protocol):
    async def resolve_approval(self, approval_reference: str) -> ApprovalGrant | None:
        """Resolve an opaque approval reference without returning its source secret."""

        ...


class ToolBudgetLedger(Protocol):
    def reserve_call(
        self,
        *,
        run_id: str,
        tool_id: str,
        policy_fingerprint: str,
        max_calls_per_run: int,
    ) -> ToolBudgetReservation:
        """Atomically reserve per Run/Tool across policy versions; never refund attempts."""

        ...


class ToolExecutor(Protocol):
    async def execute(self, call: GuardedToolCall) -> object:
        """Execute only an admitted call and enforce the attached transport limits."""

        ...
