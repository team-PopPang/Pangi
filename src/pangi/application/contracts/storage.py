"""Framework-free storage administration contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MigrationDescriptor:
    """Stable identity for one immutable package migration."""

    version: int
    name: str
    checksum: str

    def as_dict(self) -> dict[str, int | str]:
        return {
            "version": self.version,
            "name": self.name,
            "checksum": self.checksum,
        }


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """Read-only comparison between package and database migrations."""

    database_file: Path
    database_exists: bool
    applied: tuple[MigrationDescriptor, ...]
    pending: tuple[MigrationDescriptor, ...]

    @property
    def current_version(self) -> int:
        return self.applied[-1].version if self.applied else 0

    @property
    def target_version(self) -> int:
        migrations = self.applied + self.pending
        return migrations[-1].version if migrations else 0

    @property
    def backup_required(self) -> bool:
        return self.database_exists and bool(self.pending)

    def as_dict(self) -> dict[str, object]:
        return {
            "database": str(self.database_file),
            "database_exists": self.database_exists,
            "current_version": self.current_version,
            "target_version": self.target_version,
            "backup_required": self.backup_required,
            "applied": [migration.as_dict() for migration in self.applied],
            "pending": [migration.as_dict() for migration in self.pending],
        }


@dataclass(frozen=True, slots=True)
class MigrationApplyResult:
    """Result of atomically applying a pending migration batch."""

    database_file: Path
    current_version: int
    applied: tuple[MigrationDescriptor, ...]
    backup_file: Path | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "database": str(self.database_file),
            "current_version": self.current_version,
            "applied": [migration.as_dict() for migration in self.applied],
            "backup": str(self.backup_file) if self.backup_file is not None else None,
        }
