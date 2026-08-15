"""Framework-free contracts for persistent Run queue coordination."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from pangi.domain.runs import Run, RunState


@dataclass(frozen=True, slots=True)
class RunQueuePolicy:
    """Injected queue limits without fixing operational timing defaults."""

    max_concurrent_runs: int
    lease_duration: timedelta
    heartbeat_interval: timedelta

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrent_runs <= 64:
            raise ValueError("max_concurrent_runs must be between 1 and 64")
        if self.lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if self.heartbeat_interval <= timedelta(0):
            raise ValueError("heartbeat_interval must be positive")
        if self.heartbeat_interval >= self.lease_duration:
            raise ValueError("heartbeat_interval must be shorter than lease_duration")


@dataclass(frozen=True, slots=True)
class RunClaim:
    """One worker's time-bounded ownership of a running Run."""

    run: Run

    def __post_init__(self) -> None:
        if self.run.state is not RunState.RUNNING:
            raise ValueError("a Run claim requires the running state")
        if (
            self.run.worker_id is None
            or self.run.lease_expires_at is None
            or self.run.heartbeat_at is None
        ):
            raise ValueError("a Run claim requires worker, lease, and heartbeat values")

    @property
    def run_id(self) -> str:
        return self.run.id

    @property
    def worker_id(self) -> str:
        worker_id = self.run.worker_id
        if worker_id is None:  # pragma: no cover - guarded by __post_init__
            raise RuntimeError("Run claim worker is unavailable")
        return worker_id


@dataclass(frozen=True, slots=True)
class RunCancellation:
    """An idempotent cancellation result."""

    run: Run
    changed: bool

    def __post_init__(self) -> None:
        if self.run.state is not RunState.CANCELLED:
            raise ValueError("a cancellation result requires the cancelled state")


@dataclass(frozen=True, slots=True)
class RunRecoveryResult:
    """Terminal outcomes of one atomic recovery scan."""

    requeued_run_ids: tuple[str, ...] = ()
    failed_run_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.requeued_run_ids, tuple) or not isinstance(
            self.failed_run_ids, tuple
        ):
            raise ValueError("recovery Run identifiers must be immutable tuples")
        overlap = set(self.requeued_run_ids).intersection(self.failed_run_ids)
        if overlap:
            raise ValueError("a recovered Run cannot be both requeued and failed")

    @property
    def changed_count(self) -> int:
        return len(self.requeued_run_ids) + len(self.failed_run_ids)
