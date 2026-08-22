"""Ports and safe failures for Tool Approval Grant persistence."""

from __future__ import annotations

from typing import Protocol

from pangi.application.contracts.tool_approval_persistence import (
    IssuedToolApproval,
    ToolApprovalConsumption,
    ToolApprovalExpectation,
    ToolApprovalIssueCommand,
)
from pangi.application.ports.tool_guardrails import ToolApprovalConsumer


class ToolApprovalPersistenceError(RuntimeError):
    """Tool Approval governance metadata could not be persisted safely."""

    code = "tool_approval_persistence_error"


class ToolApprovalIssueDeniedError(ToolApprovalPersistenceError):
    """Current identity, Run, Tool, or Policy state cannot issue the Grant."""

    code = "tool_approval_issue_denied"


class ToolApprovalConflictError(ToolApprovalPersistenceError):
    """The generated Grant identity or reference conflicts with persisted state."""

    code = "tool_approval_conflict"


class ToolApprovalStore(ToolApprovalConsumer, Protocol):
    async def issue_grant(
        self,
        command: ToolApprovalIssueCommand,
    ) -> IssuedToolApproval:
        """Issue one exact short-lived Grant and return its raw reference once."""

        ...

    async def consume_approval(
        self,
        approval_reference: str,
        *,
        expectation: ToolApprovalExpectation,
    ) -> ToolApprovalConsumption:
        """Atomically consume one reference only when every expected claim is current."""

        ...
