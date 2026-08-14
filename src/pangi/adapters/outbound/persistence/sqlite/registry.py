"""Immutable package-resource migration registry."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib import resources
from typing import Protocol

from pangi.adapters.outbound.persistence.sqlite.errors import MigrationIntegrityError
from pangi.application.contracts.storage import MigrationDescriptor

_MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{4})_(?P<name>[a-z0-9_]+)\.sql$")
_MIGRATION_PACKAGE = "pangi.adapters.outbound.persistence.sqlite.migrations"


@dataclass(frozen=True, slots=True)
class MigrationSource:
    """Descriptor and executable SQL kept inside the outbound adapter."""

    descriptor: MigrationDescriptor
    sql: str

    @classmethod
    def from_sql(cls, version: int, name: str, sql: str) -> MigrationSource:
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        return cls(MigrationDescriptor(version, name, checksum), sql)


class MigrationRegistry(Protocol):
    def load(self) -> tuple[MigrationSource, ...]:
        """Load and validate migrations in ascending version order."""

        ...


def _validate(migrations: tuple[MigrationSource, ...]) -> tuple[MigrationSource, ...]:
    versions = [migration.descriptor.version for migration in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationIntegrityError("migration versions must be consecutive and start at 1")
    names = [migration.descriptor.name for migration in migrations]
    if len(names) != len(set(names)):
        raise MigrationIntegrityError("migration names must be unique")
    return migrations


class PackageMigrationRegistry:
    """Load SQL migrations embedded in the installed wheel."""

    def load(self) -> tuple[MigrationSource, ...]:
        package = resources.files(_MIGRATION_PACKAGE)
        migrations: list[MigrationSource] = []
        for resource in sorted(package.iterdir(), key=lambda item: item.name):
            if not resource.name.endswith(".sql"):
                continue
            matched = _MIGRATION_NAME.fullmatch(resource.name)
            if matched is None:
                raise MigrationIntegrityError("packaged migration name is invalid")
            try:
                raw = resource.read_bytes()
                sql = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise MigrationIntegrityError("packaged migration could not be read") from error
            version = int(matched.group("version"))
            name = matched.group("name")
            checksum = hashlib.sha256(raw).hexdigest()
            migrations.append(MigrationSource(MigrationDescriptor(version, name, checksum), sql))
        return _validate(tuple(migrations))


class StaticMigrationRegistry:
    """Injectable migration set used by deterministic integration tests."""

    def __init__(self, *migrations: MigrationSource) -> None:
        self._migrations = _validate(tuple(migrations))

    def load(self) -> tuple[MigrationSource, ...]:
        return self._migrations
