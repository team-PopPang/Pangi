"""Storage lifecycle ports owned by the application layer."""

from pathlib import Path
from types import TracebackType
from typing import Protocol, Self

from pangi.application.contracts.snapshots import (
    SnapshotArtifact,
    SnapshotVerification,
)
from pangi.application.contracts.storage import MigrationApplyResult, MigrationPlan


class StorageOperationError(RuntimeError):
    """Safe expected failure exposed by a storage administration adapter."""


class UnitOfWork(Protocol):
    """Framework-free transaction lifecycle shared by repository ports."""

    async def __aenter__(self) -> Self:
        """Start one transaction."""

        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Rollback an unfinished transaction and release it."""

        ...

    async def commit(self) -> None:
        """Persist the active transaction exactly once."""

        ...

    async def rollback(self) -> None:
        """Discard the active transaction exactly once."""

        ...


class UnitOfWorkFactory(Protocol):
    """Create isolated unit-of-work instances for application use cases."""

    def create(self) -> UnitOfWork:
        """Create a new, not-yet-entered unit of work."""

        ...


class DatabaseSnapshotAdmin(Protocol):
    """Create and verify database-only snapshots without exposing SQL types."""

    async def create_snapshot(self) -> SnapshotArtifact:
        """Create one verified snapshot from the active database."""

        ...

    async def verify_snapshot(self, manifest_file: Path) -> SnapshotVerification:
        """Verify one snapshot and its manifest without mutating either file."""

        ...


class MigrationAdmin(Protocol):
    """Plan and apply immutable package migrations."""

    async def plan(self) -> MigrationPlan:
        """Compare packaged migrations with the database without mutating it."""

        ...

    async def apply(self) -> MigrationApplyResult:
        """Apply the complete pending batch atomically."""

        ...
