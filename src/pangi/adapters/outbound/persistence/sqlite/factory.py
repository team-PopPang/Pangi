"""SQLite adapter composition helpers."""

from pangi.adapters.outbound.login_attempts import InMemoryLoginAttemptLimiter
from pangi.adapters.outbound.passwords import Argon2idPasswordHasher
from pangi.adapters.outbound.persistence.sqlite.auth import SqliteBootstrapStore
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.engine import SqliteMigrationAdmin
from pangi.adapters.outbound.persistence.sqlite.event_writer import SqliteRunEventWriter
from pangi.adapters.outbound.persistence.sqlite.run_events import SqliteRunEventStore
from pangi.adapters.outbound.persistence.sqlite.runs import (
    SqliteRunQueueStore,
    SqliteRunStore,
)
from pangi.adapters.outbound.persistence.sqlite.sessions import SqliteAuthSessionStore
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.contracts.run_queue import RunQueuePolicy
from pangi.application.services.auth import AuthSessionService
from pangi.application.services.bootstrap_admin import BootstrapAdminService
from pangi.application.services.run_events import (
    RunCancellationService,
    RunEventService,
    RunQueueMetricService,
)
from pangi.application.services.run_queue import RunQueueService
from pangi.application.services.runs import RunService
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)
from pangi.config import PangiConfig


def build_migration_admin(paths: RuntimePaths, config: PangiConfig) -> SqliteMigrationAdmin:
    """Build the configured migration administration adapter."""

    return SqliteMigrationAdmin(paths, config.storage)


def build_sqlite_database(paths: RuntimePaths, config: PangiConfig) -> SqliteDatabase:
    """Build the single-connection runtime database."""

    return SqliteDatabase(paths, config.storage)


def _build_run_event_writer() -> SqliteRunEventWriter:
    return SqliteRunEventWriter(core_telemetry_redaction_service())


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


def build_auth_sessions(
    database: SqliteDatabase,
    config: PangiConfig,
) -> AuthSessionService:
    """Build Local Login and persistent Session use cases."""

    password_verifier = Argon2idPasswordHasher()
    return AuthSessionService(
        SqliteAuthSessionStore(database),
        password_verifier,
        InMemoryLoginAttemptLimiter(
            attempt_limit=config.auth.login_attempt_limit,
            window_seconds=config.auth.login_attempt_window_seconds,
        ),
        dummy_password_hash=password_verifier.hash("pangi-invalid-login-placeholder"),
        session_ttl_minutes=config.auth.session_ttl_minutes,
        rotation_minutes=config.auth.session_rotation_minutes,
    )


def build_run_service(database: SqliteDatabase) -> RunService:
    """Build Run creation and owner-scoped query use cases."""

    return RunService(SqliteRunStore(database, _build_run_event_writer()))


def build_run_queue_service(
    database: SqliteDatabase,
    policy: RunQueuePolicy,
) -> RunQueueService:
    """Build persistent queue use cases with an explicitly approved timing policy."""

    return RunQueueService(
        SqliteRunQueueStore(database, _build_run_event_writer()),
        policy,
    )


def build_run_cancellation_service(
    database: SqliteDatabase,
) -> RunCancellationService:
    """Build owner-authorized Run cancellation without starting a worker runtime."""

    return RunCancellationService(
        SqliteRunStore(database, _build_run_event_writer()),
        SqliteRunQueueStore(database, _build_run_event_writer()),
    )


def build_run_event_service(database: SqliteDatabase) -> RunEventService:
    """Build owner- and visibility-scoped Run Event delivery."""

    return RunEventService(SqliteRunEventStore(database, _build_run_event_writer()))


def build_run_queue_metric_service(database: SqliteDatabase) -> RunQueueMetricService:
    """Build administrator-only Queue metric delivery."""

    return RunQueueMetricService(
        SqliteRunEventStore(database, _build_run_event_writer())
    )


def build_bootstrap_admin_for_cli(
    paths: RuntimePaths,
    config: PangiConfig,
) -> BootstrapAdminService:
    """Build a short-lived SQLite-backed Bootstrap use case for CLI commands."""

    return build_bootstrap_admin(build_sqlite_database(paths, config), config)
