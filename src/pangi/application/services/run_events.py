"""Owner-scoped Run cancellation, Event delivery, and Queue metric services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.run_events import RunEventPage, RunQueueMetrics
from pangi.application.contracts.run_queue import RunCancellation
from pangi.application.ports.auth import PermissionDeniedError
from pangi.application.ports.run_events import (
    InvalidRunEventCursorError,
    RunEventNotFoundError,
    RunEventStore,
    RunQueueMetricStore,
)
from pangi.application.ports.run_queue import (
    RunQueueNotFoundError,
    RunQueueRuntimeNotifier,
    RunQueueStore,
)
from pangi.application.ports.runs import RunNotFoundError, RunStore
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.runs import EventVisibility, RunState

Clock = Callable[[], datetime]
_TERMINAL_STATES = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _owner_scope(actor: AuthenticatedPrincipal) -> str | None:
    if actor.status is not UserStatus.ACTIVE:
        raise RunNotFoundError("The Run was not found")
    return None if actor.role is UserRole.ADMIN else actor.user_id


class RunCancellationService:
    """Authorize cancellation before entering the internal Queue boundary."""

    def __init__(
        self,
        run_store: RunStore,
        queue_store: RunQueueStore,
        *,
        runtime_notifier: RunQueueRuntimeNotifier | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._run_store = run_store
        self._queue_store = queue_store
        self._runtime_notifier = runtime_notifier
        self._clock = clock

    async def cancel_run(
        self,
        *,
        actor: AuthenticatedPrincipal,
        run_id: str,
    ) -> RunCancellation:
        owner_user_id = _owner_scope(actor)
        visible = await self._run_store.get_run(
            run_id=run_id,
            owner_user_id=owner_user_id,
        )
        if visible is None:
            raise RunNotFoundError("The Run was not found")
        at = self._clock()
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("cancellation clock must return a timezone-aware datetime")
        try:
            result = await self._queue_store.cancel(
                run_id=run_id,
                at=at.astimezone(UTC),
            )
            if result.changed and self._runtime_notifier is not None:
                self._runtime_notifier.cancel_active(run_id)
            return result
        except RunQueueNotFoundError as error:
            raise RunNotFoundError("The Run was not found") from error


class RunEventService:
    """Apply owner and visibility policy before returning Run Events."""

    def __init__(self, store: RunEventStore) -> None:
        self._store = store

    async def list_events(
        self,
        *,
        actor: AuthenticatedPrincipal,
        run_id: str,
        after_index: int,
        limit: int,
    ) -> RunEventPage:
        if after_index < 0:
            raise InvalidRunEventCursorError("The Run Event cursor is invalid")
        if not 1 <= limit <= 100:
            raise InvalidRunEventCursorError("The Run Event page limit is invalid")
        owner_user_id = _owner_scope(actor)
        visibilities: tuple[EventVisibility, ...] = (EventVisibility.PUBLIC,)
        if actor.role is UserRole.ADMIN:
            visibilities += (EventVisibility.ADMIN,)
        batch = await self._store.read_events(
            run_id=run_id,
            owner_user_id=owner_user_id,
            visibilities=visibilities,
            after_index=after_index,
            limit=limit + 1,
        )
        if batch is None:
            raise RunEventNotFoundError("The Run was not found")
        items = batch.items[:limit]
        next_after_index = None
        if len(batch.items) > limit and items:
            next_after_index = items[-1].index
        return RunEventPage(
            items=items,
            next_after_index=next_after_index,
            terminal=batch.run_state in _TERMINAL_STATES,
        )


class RunQueueMetricService:
    """Restrict identifier-free Queue measurements to active administrators."""

    def __init__(
        self,
        store: RunQueueMetricStore,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._store = store
        self._clock = clock

    async def queue_metrics(
        self,
        *,
        actor: AuthenticatedPrincipal,
    ) -> RunQueueMetrics:
        if actor.status is not UserStatus.ACTIVE or actor.role is not UserRole.ADMIN:
            raise PermissionDeniedError("The authenticated role is not allowed")
        at = self._clock()
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("metric clock must return a timezone-aware datetime")
        return await self._store.queue_metrics(at=at.astimezone(UTC))
