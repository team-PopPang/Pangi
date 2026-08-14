"""Composition root for wiring application ports to concrete adapters."""

from pathlib import Path

from pangi.adapters.inbound.cli import CliDependencies, create_app
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.factory import build_migration_admin
from pangi.adapters.outbound.runtime_control import UnavailableRuntimeControl
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.adapters.outbound.system_checks import build_doctor_service
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.ports.runtime import RuntimeBackend
from pangi.runtime import PangiRuntime


def create_runtime(backend: RuntimeBackend) -> PangiRuntime:
    """Build the public runtime facade around an application backend."""

    return PangiRuntime(backend)


def _resolve_cli_paths(project_local: bool, config_path: Path | None) -> RuntimePaths:
    return resolve_runtime_paths(project_local=project_local, explicit_config=config_path)


def build_cli_dependencies() -> CliDependencies:
    """Compose the currently available CLI use cases and local adapters."""

    return CliDependencies(
        resolve_paths=_resolve_cli_paths,
        initializer=FileSystemInitializer(),
        doctor_factory=build_doctor_service,
        migration_factory=build_migration_admin,
        runtime_control=UnavailableRuntimeControl(),
    )


def main() -> int:
    """Run the fully composed command-line adapter."""

    app = create_app(build_cli_dependencies())
    app(prog_name="pangi")
    return 0
