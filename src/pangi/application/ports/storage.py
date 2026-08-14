"""Storage administration ports owned by the application layer."""

from typing import Protocol

from pangi.application.contracts.storage import MigrationApplyResult, MigrationPlan


class StorageOperationError(RuntimeError):
    """Safe expected failure exposed by a storage administration adapter."""


class MigrationAdmin(Protocol):
    """Plan and apply immutable package migrations."""

    async def plan(self) -> MigrationPlan:
        """Compare packaged migrations with the database without mutating it."""

        ...

    async def apply(self) -> MigrationApplyResult:
        """Apply the complete pending batch atomically."""

        ...
