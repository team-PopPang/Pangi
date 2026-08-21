"""Run creation, query, and persistence boundary contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pangi.domain.runs import PrincipalChannel, Run, RunEvent, RunMode, RunState


@dataclass(frozen=True, slots=True)
class RunCreation:
    """A newly accepted Run or an exact idempotent replay."""

    run: Run
    replayed: bool


@dataclass(frozen=True, slots=True)
class RunSubmission:
    """The owner-visible Run returned after guarded orchestration submission."""

    run: Run
    replayed: bool


@dataclass(frozen=True, slots=True)
class RunCreateRecord:
    """Values a persistence adapter must commit as one transaction."""

    run: Run
    first_event: RunEvent
    route_key: str
    request_fingerprint: str
    recorded_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RunListQuery:
    """Caller-controlled Run filters without an authorization scope."""

    states: tuple[RunState, ...] = ()
    triggers: tuple[PrincipalChannel, ...] = ()
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.states, tuple) or not isinstance(self.triggers, tuple):
            raise ValueError("Run list filters must be immutable tuples")
        try:
            object.__setattr__(self, "states", tuple(RunState(state) for state in self.states))
            object.__setattr__(
                self,
                "triggers",
                tuple(PrincipalChannel(trigger) for trigger in self.triggers),
            )
        except ValueError as error:
            raise ValueError("Run list filter contains an invalid enum value") from error
        if not 1 <= self.limit <= 100:
            raise ValueError("Run list limit must be between 1 and 100")
        if self.cursor is not None and not 1 <= len(self.cursor) <= 1024:
            raise ValueError("Run list cursor must be between 1 and 1024 characters")


@dataclass(frozen=True, slots=True)
class RunCursorPosition:
    """Decoded keyset position used only after cursor validation."""

    created_at: datetime
    run_id: str


@dataclass(frozen=True, slots=True)
class RunStoreQuery:
    """Authorized query passed from the Application Service to storage."""

    owner_user_id: str | None
    states: tuple[RunState, ...]
    triggers: tuple[PrincipalChannel, ...]
    limit: int
    after: RunCursorPosition | None


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Metadata-only Run list item that does not expose normalized request text."""

    id: str
    request_id: str
    principal_id: str
    trigger: PrincipalChannel
    state: RunState
    mode: RunMode | None
    skill_version_id: str | None
    revision: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    warning_count: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class RunListPage:
    """One stable keyset page and its next opaque cursor."""

    items: tuple[RunSummary, ...]
    next_cursor: str | None
