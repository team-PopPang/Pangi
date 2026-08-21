"""Ports and safe failures for Connection Registry persistence."""

from __future__ import annotations

from typing import Protocol

from pangi.application.contracts.connections import ToolRegistrySnapshot
from pangi.application.ports.tool_guardrails import StableToolResolver
from pangi.domain.connections import Connection


class ConnectionRegistryError(RuntimeError):
    """Base class for stable, secret-safe Connection Registry failures."""

    code = "connection_registry_operation_failed"


class ConnectionRegistryNotFoundError(ConnectionRegistryError):
    code = "connection_registry_not_found"


class ConnectionRegistryConflictError(ConnectionRegistryError):
    code = "connection_registry_conflict"


class ConnectionRegistryPersistenceError(ConnectionRegistryError):
    code = "connection_registry_persistence_error"


class ConnectionRegistryStore(StableToolResolver, Protocol):
    async def add_connection(self, connection: Connection) -> None:
        """Persist one new revision-zero Connection."""

        ...

    async def get_connection(self, connection_id: str) -> Connection | None:
        """Load one Connection without exposing its Secret value."""

        ...

    async def update_connection(
        self,
        connection: Connection,
        *,
        expected_revision: int,
    ) -> None:
        """Persist exactly the next Connection revision with CAS."""

        ...

    async def save_tool_snapshot(self, snapshot: ToolRegistrySnapshot) -> None:
        """Insert or replace one strictly newer Tool discovery snapshot."""

        ...

    async def get_tool_snapshot(self, stable_tool_id: str) -> ToolRegistrySnapshot | None:
        """Load one exact Tool Registry snapshot by its globally stable ID."""

        ...
