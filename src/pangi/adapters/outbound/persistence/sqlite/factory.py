"""SQLite adapter composition helpers."""

from pangi.adapters.outbound.passwords import Argon2idPasswordHasher
from pangi.adapters.outbound.persistence.sqlite.auth import SqliteBootstrapStore
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.engine import SqliteMigrationAdmin
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.services.bootstrap_admin import BootstrapAdminService
from pangi.config import PangiConfig


def build_migration_admin(paths: RuntimePaths, config: PangiConfig) -> SqliteMigrationAdmin:
    """Build the configured migration administration adapter."""

    return SqliteMigrationAdmin(paths, config.storage)


def build_sqlite_database(paths: RuntimePaths, config: PangiConfig) -> SqliteDatabase:
    """Build the single-connection runtime database."""

    return SqliteDatabase(paths, config.storage)


def build_bootstrap_admin(
    database: SqliteDatabase,
    config: PangiConfig,
) -> BootstrapAdminService:
    """Build the Bootstrap use case against a shared SQLite runtime."""

    return BootstrapAdminService(
        SqliteBootstrapStore(database),
        Argon2idPasswordHasher(),
        public_base_url=f"http://{config.server.host}:{config.server.port}",
        grant_ttl_minutes=config.auth.bootstrap_grant_ttl_minutes,
    )


def build_bootstrap_admin_for_cli(
    paths: RuntimePaths,
    config: PangiConfig,
) -> BootstrapAdminService:
    """Build a short-lived SQLite-backed Bootstrap use case for CLI commands."""

    return build_bootstrap_admin(build_sqlite_database(paths, config), config)
