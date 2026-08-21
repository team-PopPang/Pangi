"""Ports for persistent Run queue storage and injected execution handlers."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pangi.application.contracts.run_queue import (
    RunCancellation,
    RunClaim,
    RunRecoveryResult,
)
from pangi.domain.runs import Run


class RunQueueError(RuntimeError):
    """Base class for safe queue coordination failures."""

    code = "run_queue_failed"


class RunQueueConflictError(RunQueueError):
    """A Run state or revision no longer allows the requested queue action."""

    code = "run_queue_conflict"


class RunQueueNotFoundError(RunQueueError):
    """A Run does not exist in the internal queue boundary."""

    code = "run_queue_not_found"


class RunQueuePersistenceError(RunQueueError):
    """The persistent queue is unavailable or violates its contract."""

    code = "run_queue_persistence_error"


class RunQueueUnavailableError(RunQueueError):
    """The process-local dispatcher cannot currently accept wake-ups."""

    code = "run_queue_unavailable"


class RunQueueRuntimeStatus(Protocol):
    @property
    def ready(self) -> bool:
        """Return whether the process-local dispatcher can serve queued work."""

        ...


class RunQueueRuntimeNotifier(RunQueueRuntimeStatus, Protocol):
    def wake(self) -> None:
        """Wake a healthy dispatcher after a durable Queue commit."""

        ...

    def cancel_active(self, run_id: str) -> None:
        """Signal cancellation to one process-local active handler, if present."""

        ...


class RunQueueStore(Protocol):
    async def enqueue(
        self,
        *,
        run_id: str,
        expected_revision: int,
        at: datetime,
    ) -> Run:
        """Atomically move an eligible Run into the persistent queue."""

        ...

    async def claim_next(
        self,
        *,
        worker_id: str,
        at: datetime,
        lease_expires_at: datetime,
    ) -> RunClaim | None:
        """Claim the oldest queued Run once inside a serialized transaction."""

        ...

    async def heartbeat(
        self,
        *,
        run_id: str,
        worker_id: str,
        at: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        """Extend a live running or composing lease only for its current owner."""

        ...

    async def cancel(self, *, run_id: str, at: datetime) -> RunCancellation:
        """Cancel a queued or running Run and reject stale worker writes."""

        ...

    async def recover_expired(self, *, at: datetime) -> RunRecoveryResult:
        """Recover every expired running Run using persisted Step idempotency."""

        ...

    async def abandon_claim(
        self,
        *,
        run_id: str,
        worker_id: str,
        at: datetime,
        reason: str,
    ) -> RunRecoveryResult:
        """Recover execution or fail composition after the owning handler stops."""

        ...


class RunExecutionHandler(Protocol):
    async def execute(self, claim: RunClaim) -> None:
        """Execute one claim; later WBS work supplies the real engine."""

        ...
