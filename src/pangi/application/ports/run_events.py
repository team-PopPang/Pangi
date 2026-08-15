"""Ports for Run cancellation, Event delivery, and Queue metrics."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.run_events import (
    RunEventDraft,
    RunEventPage,
    RunEventStoreBatch,
    RunQueueMetrics,
)
from pangi.application.contracts.run_queue import RunCancellation
from pangi.domain.runs import EventVisibility, RunEvent


class RunEventError(RuntimeError):
    """Base class for safe Run Event operation failures."""

    code = "run_event_failed"


class InvalidRunEventCursorError(RunEventError):
    code = "invalid_run_event_cursor"


class RunEventNotFoundError(RunEventError):
    code = "run_not_found"


class RunEventPersistenceError(RunEventError):
    code = "run_event_persistence_error"


class RunQueueMetricPersistenceError(RunEventError):
    code = "run_queue_metric_persistence_error"


class RunCancellationOperations(Protocol):
    async def cancel_run(
        self,
        *,
        actor: AuthenticatedPrincipal,
        run_id: str,
    ) -> RunCancellation:
        """Cancel one owner-visible Run without exposing foreign existence."""

        ...


class RunEventOperations(Protocol):
    async def list_events(
        self,
        *,
        actor: AuthenticatedPrincipal,
        run_id: str,
        after_index: int,
        limit: int,
    ) -> RunEventPage:
        """Read one owner- and visibility-filtered Event page."""

        ...


class RunQueueMetricOperations(Protocol):
    async def queue_metrics(
        self,
        *,
        actor: AuthenticatedPrincipal,
    ) -> RunQueueMetrics:
        """Return identifier-free Queue metrics to an administrator."""

        ...


class RunEventStore(Protocol):
    async def append_event(self, draft: RunEventDraft) -> RunEvent:
        """Atomically assign the next Run-local index and append an Event."""

        ...

    async def read_events(
        self,
        *,
        run_id: str,
        owner_user_id: str | None,
        visibilities: tuple[EventVisibility, ...],
        after_index: int,
        limit: int,
    ) -> RunEventStoreBatch | None:
        """Read Events after one index, or None outside the effective owner scope."""

        ...


class RunQueueMetricStore(Protocol):
    async def queue_metrics(self, *, at: datetime) -> RunQueueMetrics:
        """Read one consistent Queue metric snapshot."""

        ...
