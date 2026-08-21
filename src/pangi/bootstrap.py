"""Composition root for wiring application ports to concrete adapters."""

from importlib import resources
from pathlib import Path

from fastapi import FastAPI

from pangi.adapters.inbound.cli import CliDependencies, create_app
from pangi.adapters.inbound.web import create_web_app
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.logging import TelemetryRedactionFilter
from pangi.adapters.outbound.model_providers.json_schema import JsonSchemaOutputValidator
from pangi.adapters.outbound.model_providers.router import PolicySelectedModelProvider
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.factory import (
    build_audit_query_service,
    build_auth_sessions,
    build_bootstrap_admin,
    build_bootstrap_admin_for_cli,
    build_migration_admin,
    build_model_invocation_recorder,
    build_model_policy_management_service,
    build_model_policy_repository,
    build_run_cancellation_service,
    build_run_event_service,
    build_run_queue_metric_service,
    build_run_service,
    build_sqlite_database,
)
from pangi.adapters.outbound.root_catalog import EmptyRootCatalogProvider
from pangi.adapters.outbound.runtime_control import UvicornRuntimeControl
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.adapters.outbound.runtime_readiness import LocalRuntimeReadinessProbe
from pangi.adapters.outbound.system_checks import build_doctor_service
from pangi.application.contracts.model_routing import ProviderRetryPolicy
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.contracts.root_orchestration import RootOrchestratorPolicy
from pangi.application.ports.runtime import RuntimeBackend
from pangi.application.services.model_routing import (
    GuardedModelExecutionService,
    ModelPolicyService,
)
from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)
from pangi.application.services.root_orchestrator import RootOrchestratorService
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)
from pangi.config import PangiConfig
from pangi.runtime import PangiRuntime

_ROOT_PROMPT_VERSION = "root-orchestration-v1"


def create_runtime(backend: RuntimeBackend) -> PangiRuntime:
    """Build the public runtime facade around an application backend."""

    return PangiRuntime(backend)


def build_root_orchestrator_service(
    database: SqliteDatabase,
    config: PangiConfig,
) -> RootOrchestratorService:
    """Compose governed Root Model execution without starting Queue or ASGI runtime."""

    repository = build_model_policy_repository(database)
    retry_policy = ProviderRetryPolicy(
        max_attempts=config.model.max_attempts,
        attempt_timeout_seconds=config.model.attempt_timeout_seconds,
        total_timeout_seconds=config.model.total_timeout_seconds,
        retry_backoff_seconds=config.model.retry_backoff_seconds,
    )
    model = GuardedModelExecutionService(
        ModelPolicyService(
            profiles=repository,
            policies=repository,
            redactor=RedactionService(core_secret_redaction_policy()),
        ),
        provider=PolicySelectedModelProvider(retry_policy),
        output_validator=JsonSchemaOutputValidator(),
        invocations=build_model_invocation_recorder(database),
    )
    return RootOrchestratorService(
        RootOrchestratorPolicy(
            profile=config.model.root_profile,
            prompt_version=_ROOT_PROMPT_VERSION,
        ),
        catalogs=EmptyRootCatalogProvider(),
        model=model,
    )


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
