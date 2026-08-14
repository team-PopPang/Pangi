"""Framework-free database snapshot contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pangi.application.contracts.storage import MigrationDescriptor


class SnapshotKind(StrEnum):
    """Stable reasons for creating a database-only snapshot."""

    PRE_MIGRATION = "pre_migration"
    RUNTIME = "runtime"


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    """Portable integrity metadata for one SQLite snapshot."""

    manifest_schema_version: int
    backup_id: str
    kind: SnapshotKind
    created_at: datetime
    package_version: str
    snapshot_file: str
    size_bytes: int
    sha256: str
    sqlite_version: str
    user_version: int
    quick_check: str
    applied_migrations: tuple[MigrationDescriptor, ...]
    migration_target_version: int | None = None

    def as_dict(self) -> dict[str, object]:
        created_at = self.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return {
            "manifest_schema_version": self.manifest_schema_version,
            "backup_id": self.backup_id,
            "kind": self.kind.value,
            "created_at": created_at,
            "package_version": self.package_version,
            "snapshot": {
                "file": self.snapshot_file,
                "size_bytes": self.size_bytes,
                "sha256": self.sha256,
            },
            "database": {
                "sqlite_version": self.sqlite_version,
                "user_version": self.user_version,
                "quick_check": self.quick_check,
                "applied_migrations": [
                    migration.as_dict() for migration in self.applied_migrations
                ],
            },
            "migration_target_version": self.migration_target_version,
        }


@dataclass(frozen=True, slots=True)
class SnapshotArtifact:
    """Committed snapshot and its manifest sidecar."""

    snapshot_file: Path
    manifest_file: Path
    manifest: SnapshotManifest

    def as_dict(self) -> dict[str, object]:
        return {
            "snapshot": str(self.snapshot_file),
            "manifest": str(self.manifest_file),
            "metadata": self.manifest.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class SnapshotVerification:
    """Successful integrity verification with package compatibility status."""

    artifact: SnapshotArtifact
    package_compatible: bool

    def as_dict(self) -> dict[str, object]:
        return {
            **self.artifact.as_dict(),
            "integrity": "verified",
            "package_compatible": self.package_compatible,
        }
