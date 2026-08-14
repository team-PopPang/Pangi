"""Single-process SQLite persistence foundation."""

from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.engine import SqliteMigrationAdmin
from pangi.adapters.outbound.persistence.sqlite.factory import (
    build_migration_admin,
    build_sqlite_database,
)
from pangi.adapters.outbound.persistence.sqlite.snapshots import SqliteSnapshotStore
from pangi.adapters.outbound.persistence.sqlite.unit_of_work import SqliteUnitOfWork

__all__ = (
    "SqliteDatabase",
    "SqliteMigrationAdmin",
    "SqliteSnapshotStore",
    "SqliteUnitOfWork",
    "build_migration_admin",
    "build_sqlite_database",
)
