"""Composition root for wiring application ports to concrete adapters."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path

from fastapi import FastAPI

from pangi.adapters.inbound.cli import CliDependencies, create_app
from pangi.adapters.inbound.web import create_web_app
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.input_rate_limits import InMemoryInputRateLimiter
from pangi.adapters.outbound.logging import TelemetryRedactionFilter
from pangi.adapters.outbound.model_providers.json_schema import JsonSchemaOutputValidator
from pangi.adapters.outbound.model_providers.router import PolicySelectedModelProvider
from pangi.adapters.outbound.orchestration import UnavailableOrchestrationTaskExecutor
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.event_writer import SqliteRunEventWriter
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
    build_run_queue_service,
    build_run_service,
    build_sqlite_database,
)
from pangi.adapters.outbound.persistence.sqlite.orchestration_execution import (
    SqliteOrchestrationExecutionStore,
)
from pangi.adapters.outbound.persistence.sqlite.orchestration_lifecycle import (
    SqliteOrchestrationLifecycleStore,
)
from pangi.adapters.outbound.root_catalog import EmptyRootCatalogProvider
from pangi.adapters.outbound.runtime_control import UvicornRuntimeControl
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.adapters.outbound.runtime_readiness import LocalRuntimeReadinessProbe
from pangi.adapters.outbound.skill_authorization import UnavailableExplicitSkillAuthorizer
from pangi.adapters.outbound.system_checks import build_doctor_service
from pangi.application.contracts.guardrails import InputGuardrailPolicy
from pangi.application.contracts.model_routing import ProviderRetryPolicy
from pangi.application.contracts.orchestration_execution import ExecutionPolicy
from pangi.application.contracts.output_guardrails import OutputGuardrailPolicy
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.contracts.root_orchestration import RootOrchestratorPolicy
from pangi.application.contracts.run_queue import RunQueuePolicy
from pangi.application.ports.runtime import RuntimeBackend
from pangi.application.services.execution_engine import DependencyExecutionEngine
from pangi.application.services.input_guardrails import (
    GuardedRunSubmissionService,
    InputGuardrailService,
)
from pangi.application.services.model_routing import (
    GuardedModelExecutionService,
    ModelPolicyService,
)
from pangi.application.services.orchestration_lifecycle import (
    OrchestrationRunHandler,
    OrchestrationSubmissionService,
)
from pangi.application.services.output_guardrails import (
    OutputGuardrailService,
    core_output_internal_detail_rules,
)
from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)
from pangi.application.services.result_reducer import OrchestrationOutputComposer
from pangi.application.services.root_orchestrator import RootOrchestratorService
from pangi.application.services.run_events import RunCancellationService
from pangi.application.services.run_queue import RunQueueRuntime
from pangi.application.services.run_submissions import LocalRunSubmissionService
from pangi.application.services.runtime_lifecycle import CompositeRuntimeBackend
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)
from pangi.config import PangiConfig
from pangi.domain.model_routing import DataClass
from pangi.runtime import PangiRuntime

_ROOT_PROMPT_VERSION = "root-orchestration-v1"
_INPUT_POLICY_VERSION = "local-dashboard-input-v1"
_OUTPUT_POLICY_VERSION = "local-orchestration-output-v1"


@dataclass(frozen=True, slots=True)
class _RunRuntime:
    submissions: LocalRunSubmissionService
    cancellations: RunCancellationService
    queue: RunQueueRuntime


def _local_input_policy() -> InputGuardrailPolicy:
    prohibited = frozenset(
        {
            0x200B,
            0x202A,
            0x202B,
            0x202C,
            0x202D,
            0x202E,
            0x2066,
            0x2067,
            0x2068,
            0x2069,
            0xFEFF,
        }
    )
    return InputGuardrailPolicy(
        policy_version=_INPUT_POLICY_VERSION,
        unicode_policy_version="local-unicode-v1",
        max_text_bytes=100_000,
        max_attachment_count=0,
        max_attachment_bytes=0,
        max_total_attachment_bytes=0,
        allowed_media_types=frozenset(),
        prohibited_codepoints=prohibited,
        rate_limit=60,
        rate_window_seconds=60,
    )


def _local_output_policy() -> OutputGuardrailPolicy:
    return OutputGuardrailPolicy(
        policy_version=_OUTPUT_POLICY_VERSION,
        max_input_bytes=100_000,
        max_output_bytes=50_000,
        max_mentions=2,
        max_evidence_links=20,
        max_evidence_link_bytes=2_048,
        allowed_link_schemes=frozenset({"https"}),
        allow_relative_links=False,
        broadcast_mentions=frozenset({"@channel", "@everyone", "@here"}),
        internal_detail_rules=core_output_internal_detail_rules(),
        truncation_marker="\n\n[OUTPUT TRUNCATED]",
    )


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


def _build_run_runtime(
    database: SqliteDatabase,
    config: PangiConfig,
) -> _RunRuntime:
    event_writer = SqliteRunEventWriter(core_telemetry_redaction_service())
    executions = SqliteOrchestrationExecutionStore(
        database,
        RedactionService(core_secret_redaction_policy()),
        event_writer,
    )
    lifecycle = SqliteOrchestrationLifecycleStore(database, event_writer)
    engine = DependencyExecutionEngine(
        executions,
        UnavailableOrchestrationTaskExecutor(),
        ExecutionPolicy(max_parallel_steps=1),
    )
    composer = OrchestrationOutputComposer(
        OutputGuardrailService(
            _local_output_policy(),
            redactor=RedactionService(core_secret_redaction_policy()),
        )
    )
    handler = OrchestrationRunHandler(
        executions,
        runner=engine,
        composer=composer,
        lifecycle=lifecycle,
    )
    queue = RunQueueRuntime(
        build_run_queue_service(
            database,
            RunQueuePolicy(
                max_concurrent_runs=config.runtime.max_concurrent_runs,
                lease_duration=timedelta(seconds=30),
                heartbeat_interval=timedelta(seconds=10),
            ),
        ),
        handler,
    )
    runs = build_run_service(database)
    guarded_runs = GuardedRunSubmissionService(
        InputGuardrailService(
            _local_input_policy(),
            skill_authorizer=UnavailableExplicitSkillAuthorizer(),
            rate_limiter=InMemoryInputRateLimiter(max_keys=10_000),
            clock=lambda: datetime.now(UTC),
        ),
        run_creator=runs,
    )
    orchestrator = OrchestrationSubmissionService(
        lifecycle,
        root=build_root_orchestrator_service(database, config),
        materializer=engine,
    )
    submissions = LocalRunSubmissionService(
        guarded_runs,
        orchestrator=orchestrator,
        runs=runs,
        queue=queue,
        data_classes=frozenset(
            DataClass(value) for value in config.runtime.run_data_classes
        ),
    )
    cancellations = build_run_cancellation_service(
        database,
        runtime_notifier=queue,
    )
    return _RunRuntime(submissions, cancellations, queue)


def _resolve_cli_paths(project_local: bool, config_path: Path | None) -> RuntimePaths:
    return resolve_runtime_paths(project_local=project_local, explicit_config=config_path)


def create_asgi_app(paths: RuntimePaths, config: PangiConfig) -> FastAPI:
    """Compose the local SQLite runtime and packaged Admin Web adapter."""

    database = build_sqlite_database(paths, config)
    run_runtime = _build_run_runtime(database, config)
    runtime_backend = CompositeRuntimeBackend((database, run_runtime.queue))
    static_root = Path(str(resources.files("pangi.web").joinpath("static")))
    return create_web_app(
        runtime_backend=runtime_backend,
        readiness_probe=LocalRuntimeReadinessProbe(
            database,
            queue_runtime=run_runtime.queue,
        ),
        audit_operations=build_audit_query_service(database),
        bootstrap_admin=build_bootstrap_admin(database, config),
        auth_sessions=build_auth_sessions(database, config),
        run_operations=build_run_service(database),
        run_cancellations=run_runtime.cancellations,
        run_events=build_run_event_service(database),
        run_queue_metrics=build_run_queue_metric_service(database),
        run_submissions=run_runtime.submissions,
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
