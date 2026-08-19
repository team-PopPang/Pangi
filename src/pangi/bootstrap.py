"""Composition root for wiring application ports to concrete adapters."""

from importlib import resources
from pathlib import Path

from fastapi import FastAPI

from pangi.adapters.inbound.cli import CliDependencies, create_app
from pangi.adapters.inbound.web import create_web_app
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.logging import TelemetryRedactionFilter
from pangi.adapters.outbound.persistence.sqlite.factory import (
    build_audit_query_service,
    build_auth_sessions,
    build_bootstrap_admin,
    build_bootstrap_admin_for_cli,
    build_migration_admin,
    build_model_policy_management_service,
    build_run_cancellation_service,
    build_run_event_service,
    build_run_queue_metric_service,
    build_run_service,
    build_sqlite_database,
)
from pangi.adapters.outbound.runtime_control import UvicornRuntimeControl
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.adapters.outbound.runtime_readiness import LocalRuntimeReadinessProbe
from pangi.adapters.outbound.system_checks import build_doctor_service
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.ports.runtime import RuntimeBackend
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)
from pangi.config import PangiConfig
from pangi.runtime import PangiRuntime


def create_runtime(backend: RuntimeBackend) -> PangiRuntime:
    """Build the public runtime facade around an application backend."""

    return PangiRuntime(backend)


def _resolve_cli_paths(project_local: bool, config_path: Path | None) -> RuntimePaths:
    return resolve_runtime_paths(project_local=project_local, explicit_config=config_path)


def create_asgi_app(paths: RuntimePaths, config: PangiConfig) -> FastAPI:
    """Compose the local SQLite runtime and packaged Admin Web adapter."""

    database = build_sqlite_database(paths, config)
    static_root = Path(str(resources.files("pangi.web").joinpath("static")))
    return create_web_app(
        runtime_backend=database,
        readiness_probe=LocalRuntimeReadinessProbe(database),
        audit_operations=build_audit_query_service(database),
        bootstrap_admin=build_bootstrap_admin(database, config),
        auth_sessions=build_auth_sessions(database, config),
        run_operations=build_run_service(database),
        run_cancellations=build_run_cancellation_service(database),
        run_events=build_run_event_service(database),
        run_queue_metrics=build_run_queue_metric_service(database),
        model_policy_operations=build_model_policy_management_service(database),
        static_root=static_root,
    )


def _build_runtime_control(
    paths: RuntimePaths,
    config: PangiConfig,
) -> UvicornRuntimeControl:
    return UvicornRuntimeControl(
        app=create_asgi_app(paths, config),
        host=config.server.host,
        port=config.server.port,
        telemetry_filter=TelemetryRedactionFilter(core_telemetry_redaction_service()),
    )


def build_cli_dependencies() -> CliDependencies:
    """Compose the currently available CLI use cases and local adapters."""

    return CliDependencies(
        resolve_paths=_resolve_cli_paths,
        initializer=FileSystemInitializer(),
        doctor_factory=build_doctor_service,
        migration_factory=build_migration_admin,
        runtime_control_factory=_build_runtime_control,
        bootstrap_admin_factory=build_bootstrap_admin_for_cli,
    )


def main() -> int:
    """Run the fully composed command-line adapter."""

    app = create_app(build_cli_dependencies())
    app(prog_name="pangi")
    return 0
