"""SQLite adapter composition helpers."""

from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.engine import SqliteMigrationAdmin
from pangi.application.contracts.paths import RuntimePaths
from pangi.config import PangiConfig


def build_migration_admin(paths: RuntimePaths, config: PangiConfig) -> SqliteMigrationAdmin:
    """Build the configured migration administration adapter."""

    return SqliteMigrationAdmin(paths, config.storage)


def build_sqlite_database(paths: RuntimePaths, config: PangiConfig) -> SqliteDatabase:
    """Build the single-connection runtime database."""

    return SqliteDatabase(paths, config.storage)
