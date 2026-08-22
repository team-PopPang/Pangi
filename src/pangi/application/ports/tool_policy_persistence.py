"""Ports and safe failures for Tool Policy persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pangi.application.contracts.tool_guardrails import ToolPolicy
from pangi.application.contracts.tool_policy_persistence import (
    ToolPolicyActivation,
    ToolPolicyActivationCommand,
    ToolPolicyVersion,
)
from pangi.application.ports.tool_guardrails import ToolPolicyProvider


class ToolPolicyPersistenceError(RuntimeError):
    """Tool Policy governance metadata could not be persisted safely."""

    code = "tool_policy_persistence_error"


class ToolPolicyConflictError(ToolPolicyPersistenceError):
    code = "tool_policy_conflict"


class ToolPolicyStaleActivationError(ToolPolicyConflictError):
    code = "tool_policy_stale_activation"


class ToolBudgetPersistenceError(RuntimeError):
    """A durable Tool Call Budget could not be reserved safely."""

    code = "tool_budget_persistence_error"


class ToolPolicyStore(ToolPolicyProvider, Protocol):
    async def save_draft(self, policy: ToolPolicy, *, at: datetime) -> None:
        """Append one immutable draft Tool Policy version."""

        ...

    async def get_version(
        self,
        tool_id: str,
        policy_version: str,
    ) -> ToolPolicyVersion | None:
        """Load one exact Tool Policy version."""

        ...

    async def activate(
        self,
        command: ToolPolicyActivationCommand,
    ) -> ToolPolicyActivation:
        """Activate a draft only if its candidate and baseline remain current."""

        ...
