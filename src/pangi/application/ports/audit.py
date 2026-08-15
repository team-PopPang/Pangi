"""Ports for append-only Audit persistence and administrator queries."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pangi.application.contracts.audit import (
    AuditEventDraft,
    AuditListPage,
    AuditListQuery,
    AuditStoreQuery,
)
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.domain.audit import AuditEvent


class AuditOperationError(RuntimeError):
    """Base class for expected, secret-safe Audit failures."""

    code = "audit_operation_failed"


class InvalidAuditCursorError(AuditOperationError):
    code = "invalid_audit_cursor"


class AuditPersistenceError(AuditOperationError):
    code = "audit_persistence_error"


class AuditOperations(Protocol):
    async def list_events(
        self,
        *,
        actor: AuthenticatedPrincipal,
        query: AuditListQuery,
    ) -> AuditListPage:
        """Return one stable page to an active administrator."""

        ...


class AuditEventStore(Protocol):
    async def append_event(self, draft: AuditEventDraft) -> AuditEvent:
        """Append one standalone event through the final safe write boundary."""

        ...

    async def list_events(self, query: AuditStoreQuery) -> tuple[AuditEvent, ...]:
        """Read at most query.limit immutable events in descending keyset order."""

        ...

    async def purge_expired(self, *, before: datetime, limit: int) -> int:
        """Delete only retention-expired events through the maintenance boundary."""

        ...
