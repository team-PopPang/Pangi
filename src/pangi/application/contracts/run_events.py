"""Framework-free Run Event delivery and queue metric contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from pangi.domain.runs import EventVisibility, RunEvent, RunState


@dataclass(frozen=True, slots=True)
class RunEventDraft:
    """A safe Event payload whose Run-local index is assigned by storage."""

    run_id: str
    type: str
    visibility: EventVisibility
    created_at: datetime
    step_id: str | None = None
    message: str | None = None
    attributes: Mapping[str, object] | None = None

    def to_event(self, *, index: int) -> RunEvent:
        return RunEvent(
            run_id=self.run_id,
            index=index,
            type=self.type,
            visibility=self.visibility,
            created_at=self.created_at,
            step_id=self.step_id,
            message=self.message,
            attributes={} if self.attributes is None else self.attributes,
        )


@dataclass(frozen=True, slots=True)
class RunEventStoreBatch:
    """One visibility-filtered storage read and the current Run state."""

    items: tuple[RunEvent, ...]
    run_state: RunState


@dataclass(frozen=True, slots=True)
class RunEventPage:
    """One Event page shared by JSON pagination and SSE polling."""

    items: tuple[RunEvent, ...]
    next_after_index: int | None
    terminal: bool


@dataclass(frozen=True, slots=True)
class RunEventStreamPolicy:
    """Injectable polling limits for the process-local SSE adapter."""

    batch_size: int = 100
    poll_interval_seconds: float = 1.0
    keepalive_interval_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not 1 <= self.batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.keepalive_interval_seconds < self.poll_interval_seconds:
            raise ValueError(
                "keepalive_interval_seconds must not be shorter than poll_interval_seconds"
            )


@dataclass(frozen=True, slots=True)
class RunQueueMetrics:
    """Identifier-free operational Queue measurements."""

    queue_depth: int
    running_count: int
    expired_lease_count: int
    oldest_queued_at: datetime | None
    oldest_queued_age_seconds: float | None

    def __post_init__(self) -> None:
        for name in ("queue_depth", "running_count", "expired_lease_count"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.oldest_queued_at is None:
            if self.oldest_queued_age_seconds is not None:
                raise ValueError("an oldest Queue age requires a timestamp")
            return
        value = self.oldest_queued_at
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("oldest_queued_at must be timezone-aware")
        object.__setattr__(self, "oldest_queued_at", value.astimezone(UTC))
        if self.oldest_queued_age_seconds is None or self.oldest_queued_age_seconds < 0:
            raise ValueError("oldest_queued_age_seconds must be non-negative")
