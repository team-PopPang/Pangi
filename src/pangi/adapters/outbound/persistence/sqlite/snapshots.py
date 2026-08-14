"""Atomic SQLite snapshot creation, manifest parsing, and verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import aiosqlite

from pangi._version import __version__
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all, fetch_one
from pangi.adapters.outbound.persistence.sqlite.errors import (
    SnapshotError,
    SnapshotIntegrityError,
    StorageSafetyError,
)
from pangi.adapters.outbound.persistence.sqlite.filesystem import ensure_local_filesystem
from pangi.adapters.outbound.persistence.sqlite.registry import (
    MigrationRegistry,
    PackageMigrationRegistry,
)
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.contracts.snapshots import (
    SnapshotArtifact,
    SnapshotKind,
    SnapshotManifest,
    SnapshotVerification,
)
from pangi.application.contracts.storage import MigrationDescriptor

_MANIFEST_SCHEMA_VERSION = 1
_MAX_MANIFEST_BYTES = 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)

Now = Callable[[], datetime]
Backup = Callable[
    [aiosqlite.Connection, aiosqlite.Connection],
    Awaitable[None],
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _backup_database(
    source: aiosqlite.Connection,
    target: aiosqlite.Connection,
) -> None:
    await source.backup(target)


@dataclass(frozen=True, slots=True)
class _DatabaseInfo:
    sqlite_version: str
    user_version: int
    quick_check: str
    applied_migrations: tuple[MigrationDescriptor, ...]


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _expect_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SnapshotIntegrityError(f"snapshot manifest field is invalid: {field}")
    return value


def _expect_keys(value: dict[str, object], keys: set[str], field: str) -> None:
    if set(value) != keys:
        raise SnapshotIntegrityError(f"snapshot manifest shape is invalid: {field}")


def _expect_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SnapshotIntegrityError(f"snapshot manifest field is invalid: {field}")
    return value


def _expect_integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SnapshotIntegrityError(f"snapshot manifest field is invalid: {field}")
    return value


def _expect_optional_integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _expect_integer(value, field)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


class SqliteSnapshotStore:
    """Commit a snapshot only after its database and manifest are verified."""

    def __init__(
        self,
        paths: RuntimePaths,
        *,
        registry: MigrationRegistry | None = None,
        now: Now = _utc_now,
        package_version: str = __version__,
        backup: Backup = _backup_database,
    ) -> None:
        self.paths = paths
        self._registry = PackageMigrationRegistry() if registry is None else registry
        self._now = now
        self._package_version = package_version
        self._backup = backup

    def _backup_directory(self) -> Path:
        backup_dir = self.paths.backup_dir.absolute()
        if backup_dir.is_symlink() or not backup_dir.is_dir():
            raise StorageSafetyError("SQLite backup directory is missing or unsafe")
        ensure_local_filesystem(backup_dir)
        return backup_dir

    @staticmethod
    def _private_file(path: Path) -> None:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _CLOEXEC | _NOFOLLOW,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_manifest(path: Path, manifest: SnapshotManifest) -> None:
        payload = json.dumps(
            manifest.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _CLOEXEC | _NOFOLLOW,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | _CLOEXEC)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _hash_and_size(path: Path) -> tuple[str, int]:
        descriptor = os.open(path, os.O_RDONLY | _CLOEXEC | _NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise SnapshotIntegrityError("snapshot is not a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as snapshot:
                checksum = hashlib.file_digest(snapshot, "sha256").hexdigest()
            return checksum, metadata.st_size
        finally:
            os.close(descriptor)

    @staticmethod
    async def _inspect_connection(connection: aiosqlite.Connection) -> _DatabaseInfo:
        quick_check = await fetch_one(connection, "PRAGMA quick_check")
        user_version = await fetch_one(connection, "PRAGMA user_version")
        sqlite_version = await fetch_one(connection, "SELECT sqlite_version()")
        if quick_check is None or user_version is None or sqlite_version is None:
            raise SnapshotIntegrityError("snapshot database metadata is incomplete")

        table = await fetch_one(
            connection,
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("schema_migrations",),
        )
        migrations: tuple[MigrationDescriptor, ...] = ()
        if table is not None:
            rows = await fetch_all(
                connection,
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version",
            )
            migrations = tuple(
                MigrationDescriptor(int(row[0]), str(row[1]), str(row[2])) for row in rows
            )
        for expected_version, migration in enumerate(migrations, start=1):
            if migration.version != expected_version or not _is_sha256(migration.checksum):
                raise SnapshotIntegrityError("snapshot migration history is invalid")
        parsed_user_version = int(user_version[0])
        expected_user_version = migrations[-1].version if migrations else 0
        if parsed_user_version != expected_user_version:
            raise SnapshotIntegrityError("snapshot schema version is inconsistent")
        return _DatabaseInfo(
            sqlite_version=str(sqlite_version[0]),
            user_version=parsed_user_version,
            quick_check=str(quick_check[0]).lower(),
            applied_migrations=migrations,
        )

    @staticmethod
    async def _open_read_only(path: Path) -> aiosqlite.Connection:
        encoded = quote(str(path.absolute()), safe="/")
        return await aiosqlite.connect(
            f"file:{encoded}?mode=ro",
            uri=True,
            isolation_level=None,
        )

    async def _inspect_file(self, path: Path) -> _DatabaseInfo:
        connection = await self._open_read_only(path)
        try:
            return await self._inspect_connection(connection)
        except aiosqlite.Error as error:
            raise SnapshotIntegrityError("snapshot database could not be inspected") from error
        finally:
            await connection.close()

    def _candidate(
        self,
        backup_dir: Path,
        kind: SnapshotKind,
        current_version: int,
        target_version: int | None,
        created_at: datetime,
    ) -> Path:
        timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
        if kind is SnapshotKind.PRE_MIGRATION:
            if target_version is None:
                raise SnapshotError("pre-migration snapshot requires a target version")
            if target_version <= current_version:
                raise SnapshotError("pre-migration snapshot target must be newer")
            stem = f"pre-migrate-v{current_version}-to-v{target_version}-{timestamp}"
        else:
            if target_version is not None:
                raise SnapshotError("runtime snapshot cannot declare a migration target")
            stem = f"runtime-v{current_version}-{timestamp}"

        candidate = backup_dir / f"{stem}.sqlite3"
        counter = 1
        while candidate.exists() or candidate.with_name(
            f"{candidate.name}.manifest.json"
        ).exists():
            candidate = backup_dir / f"{stem}-{counter}.sqlite3"
            counter += 1
        return candidate

    async def create(
        self,
        source: aiosqlite.Connection,
        *,
        kind: SnapshotKind,
        migration_target_version: int | None = None,
    ) -> SnapshotArtifact:
        """Create and atomically commit one verified snapshot pair."""

        backup_dir = self._backup_directory()
        created_at = self._now()
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise SnapshotError("snapshot creation time must include a timezone")
        created_at = created_at.astimezone(UTC)
        current_version = await fetch_one(source, "PRAGMA user_version")
        if current_version is None:
            raise SnapshotError("SQLite schema version could not be read")
        snapshot_file = self._candidate(
            backup_dir,
            kind,
            int(current_version[0]),
            migration_target_version,
            created_at,
        )
        manifest_file = snapshot_file.with_name(f"{snapshot_file.name}.manifest.json")
        nonce = uuid4().hex
        partial_snapshot = backup_dir / f".{snapshot_file.name}.{nonce}.partial"
        partial_manifest = backup_dir / f".{manifest_file.name}.{nonce}.partial"
        committed_snapshot = False
        committed_manifest = False
        completed = False
        target: aiosqlite.Connection | None = None

        try:
            self._private_file(partial_snapshot)
            target = await aiosqlite.connect(partial_snapshot, isolation_level=None)
            await self._backup(source, target)
            await target.close()
            target = None
            partial_snapshot.chmod(0o600)

            database = await self._inspect_file(partial_snapshot)
            if database.quick_check != "ok":
                raise SnapshotIntegrityError("snapshot quick_check failed")
            checksum, size_bytes = self._hash_and_size(partial_snapshot)
            manifest = SnapshotManifest(
                manifest_schema_version=_MANIFEST_SCHEMA_VERSION,
                backup_id=uuid4().hex,
                kind=kind,
                created_at=created_at,
                package_version=self._package_version,
                snapshot_file=snapshot_file.name,
                size_bytes=size_bytes,
                sha256=checksum,
                sqlite_version=database.sqlite_version,
                user_version=database.user_version,
                quick_check=database.quick_check,
                applied_migrations=database.applied_migrations,
                migration_target_version=migration_target_version,
            )
            self._write_manifest(partial_manifest, manifest)

            os.link(partial_snapshot, snapshot_file, follow_symlinks=False)
            committed_snapshot = True
            partial_snapshot.unlink()
            os.link(partial_manifest, manifest_file, follow_symlinks=False)
            committed_manifest = True
            partial_manifest.unlink()
            self._fsync_directory(backup_dir)
            verification = await self.verify(manifest_file)
            completed = True
            return verification.artifact
        except asyncio.CancelledError:
            raise
        except SnapshotError:
            raise
        except Exception as error:
            raise SnapshotError("SQLite snapshot creation failed") from error
        finally:
            try:
                if target is not None:
                    await target.close()
            finally:
                _safe_unlink(partial_snapshot)
                _safe_unlink(partial_manifest)
                if not completed and committed_manifest:
                    _safe_unlink(manifest_file)
                if not completed and committed_snapshot:
                    _safe_unlink(snapshot_file)

    @staticmethod
    def _read_manifest_file(manifest_file: Path) -> dict[str, object]:
        descriptor = os.open(manifest_file, os.O_RDONLY | _CLOEXEC | _NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_MANIFEST_BYTES:
                raise SnapshotIntegrityError("snapshot manifest file is invalid")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise SnapshotIntegrityError("snapshot manifest permissions are unsafe")
            with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as manifest:
                raw: object = json.load(manifest)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise SnapshotIntegrityError("snapshot manifest could not be read") from error
        finally:
            os.close(descriptor)
        return _expect_mapping(raw, "document")

    @staticmethod
    def _parse_manifest(raw: dict[str, object]) -> SnapshotManifest:
        _expect_keys(
            raw,
            {
                "manifest_schema_version",
                "backup_id",
                "kind",
                "created_at",
                "package_version",
                "snapshot",
                "database",
                "migration_target_version",
            },
            "document",
        )
        manifest_version = _expect_integer(
            raw["manifest_schema_version"],
            "manifest_schema_version",
            minimum=1,
        )
        if manifest_version != _MANIFEST_SCHEMA_VERSION:
            raise SnapshotIntegrityError("snapshot manifest version is unsupported")

        backup_id = _expect_string(raw["backup_id"], "backup_id")
        try:
            if UUID(hex=backup_id).hex != backup_id:
                raise ValueError
        except ValueError as error:
            raise SnapshotIntegrityError("snapshot backup ID is invalid") from error

        try:
            kind = SnapshotKind(_expect_string(raw["kind"], "kind"))
        except ValueError as error:
            raise SnapshotIntegrityError("snapshot kind is invalid") from error

        created_text = _expect_string(raw["created_at"], "created_at")
        try:
            created_at = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
        except ValueError as error:
            raise SnapshotIntegrityError("snapshot creation time is invalid") from error
        if created_at.tzinfo is None or created_at.utcoffset() != UTC.utcoffset(created_at):
            raise SnapshotIntegrityError("snapshot creation time must be UTC")

        snapshot = _expect_mapping(raw["snapshot"], "snapshot")
        _expect_keys(snapshot, {"file", "size_bytes", "sha256"}, "snapshot")
        snapshot_name = _expect_string(snapshot["file"], "snapshot.file")
        if (
            Path(snapshot_name).name != snapshot_name
            or "/" in snapshot_name
            or "\\" in snapshot_name
            or not snapshot_name.endswith(".sqlite3")
        ):
            raise SnapshotIntegrityError("snapshot file name is unsafe")
        checksum = _expect_string(snapshot["sha256"], "snapshot.sha256")
        if not _is_sha256(checksum):
            raise SnapshotIntegrityError("snapshot checksum is invalid")

        database = _expect_mapping(raw["database"], "database")
        _expect_keys(
            database,
            {"sqlite_version", "user_version", "quick_check", "applied_migrations"},
            "database",
        )
        migrations_raw = database["applied_migrations"]
        if not isinstance(migrations_raw, list):
            raise SnapshotIntegrityError("snapshot migration history is invalid")
        migrations: list[MigrationDescriptor] = []
        for index, item in enumerate(migrations_raw, start=1):
            migration = _expect_mapping(item, f"database.applied_migrations[{index}]")
            _expect_keys(migration, {"version", "name", "checksum"}, "migration")
            version = _expect_integer(migration["version"], "migration.version", minimum=1)
            migration_checksum = _expect_string(migration["checksum"], "migration.checksum")
            if version != index or not _is_sha256(migration_checksum):
                raise SnapshotIntegrityError("snapshot migration history is invalid")
            migrations.append(
                MigrationDescriptor(
                    version,
                    _expect_string(migration["name"], "migration.name"),
                    migration_checksum,
                )
            )

        target_version = _expect_optional_integer(
            raw["migration_target_version"],
            "migration_target_version",
        )
        if (kind is SnapshotKind.PRE_MIGRATION) != (target_version is not None):
            raise SnapshotIntegrityError("snapshot migration target is inconsistent")
        quick_check = _expect_string(database["quick_check"], "database.quick_check")
        if quick_check != "ok":
            raise SnapshotIntegrityError("snapshot manifest records a failed quick_check")

        user_version = _expect_integer(database["user_version"], "database.user_version")
        expected_user_version = migrations[-1].version if migrations else 0
        if user_version != expected_user_version:
            raise SnapshotIntegrityError("snapshot schema version is inconsistent")
        if target_version is not None and target_version <= user_version:
            raise SnapshotIntegrityError("snapshot migration target is invalid")

        return SnapshotManifest(
            manifest_schema_version=manifest_version,
            backup_id=backup_id,
            kind=kind,
            created_at=created_at,
            package_version=_expect_string(raw["package_version"], "package_version"),
            snapshot_file=snapshot_name,
            size_bytes=_expect_integer(snapshot["size_bytes"], "snapshot.size_bytes", minimum=1),
            sha256=checksum,
            sqlite_version=_expect_string(database["sqlite_version"], "database.sqlite_version"),
            user_version=user_version,
            quick_check=quick_check,
            applied_migrations=tuple(migrations),
            migration_target_version=target_version,
        )

    def _package_compatible(self, applied: tuple[MigrationDescriptor, ...]) -> bool:
        packaged = tuple(migration.descriptor for migration in self._registry.load())
        return len(applied) <= len(packaged) and applied == packaged[: len(applied)]

    async def verify(self, manifest_file: Path) -> SnapshotVerification:
        """Verify one committed pair without modifying the artifact."""

        backup_dir = self._backup_directory()
        candidate = manifest_file.absolute()
        if candidate.parent != backup_dir or candidate.is_symlink() or not candidate.is_file():
            raise StorageSafetyError("snapshot manifest path is unsafe")
        raw = self._read_manifest_file(candidate)
        manifest = self._parse_manifest(raw)
        if candidate.name != f"{manifest.snapshot_file}.manifest.json":
            raise SnapshotIntegrityError("snapshot manifest sidecar name is inconsistent")

        snapshot_file = backup_dir / manifest.snapshot_file
        if snapshot_file.is_symlink() or not snapshot_file.is_file():
            raise StorageSafetyError("snapshot file path is unsafe")
        metadata = snapshot_file.stat()
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise SnapshotIntegrityError("snapshot permissions are unsafe")
        checksum, size_bytes = self._hash_and_size(snapshot_file)
        if checksum != manifest.sha256 or size_bytes != manifest.size_bytes:
            raise SnapshotIntegrityError("snapshot checksum or size does not match manifest")

        database = await self._inspect_file(snapshot_file)
        if database.quick_check != "ok":
            raise SnapshotIntegrityError("snapshot quick_check failed")
        if (
            database.user_version != manifest.user_version
            or database.applied_migrations != manifest.applied_migrations
        ):
            raise SnapshotIntegrityError("snapshot schema history does not match manifest")
        artifact = SnapshotArtifact(snapshot_file, candidate, manifest)
        return SnapshotVerification(
            artifact=artifact,
            package_compatible=self._package_compatible(database.applied_migrations),
        )

    def latest_manifest(self) -> tuple[Path | None, bool]:
        """Return the newest manifest and whether unverified legacy files exist."""

        backup_dir = self._backup_directory()
        manifests: list[Path] = []
        has_legacy = False
        for candidate in backup_dir.iterdir():
            if candidate.name.endswith(".sqlite3.manifest.json"):
                manifests.append(candidate)
            elif candidate.name.endswith(".sqlite3") and not candidate.with_name(
                f"{candidate.name}.manifest.json"
            ).exists():
                has_legacy = True
        if not manifests:
            return None, has_legacy
        latest = max(manifests, key=lambda path: path.lstat().st_mtime_ns)
        return latest, has_legacy
