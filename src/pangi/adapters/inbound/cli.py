"""Stable command-line adapter with injected application dependencies."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn, Protocol

import typer

from pangi._version import __version__
from pangi.adapters.inbound.output import redact_text, render_json
from pangi.application.contracts.bootstrap import BootstrapIssueResult, BootstrapIssueStatus
from pangi.application.contracts.initialization import InitActionKind, InitPlan, InitResult
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.contracts.runtime_status import RuntimeState
from pangi.application.ports.bootstrap_admin import (
    BootstrapAdminPort,
    BootstrapOperationError,
)
from pangi.application.ports.runtime_control import RuntimeControl, RuntimeUnavailableError
from pangi.application.ports.storage import MigrationAdmin, StorageOperationError
from pangi.application.services.doctor import DoctorService
from pangi.config import PangiConfig, PangiConfigError

PathResolver = Callable[[bool, Path | None], RuntimePaths]
DoctorFactory = Callable[[RuntimePaths, PangiConfig], DoctorService]
MigrationFactory = Callable[[RuntimePaths, PangiConfig], MigrationAdmin]
RuntimeControlFactory = Callable[[RuntimePaths, PangiConfig], RuntimeControl]
BootstrapAdminFactory = Callable[[RuntimePaths, PangiConfig], BootstrapAdminPort]


class RuntimeInitializer(Protocol):
    def plan(self, paths: RuntimePaths) -> InitPlan:
        """Create a mutation-free initialization plan."""

        ...

    def apply(self, plan: InitPlan, config_text: str) -> InitResult:
        """Apply a previously validated plan without overwriting user files."""

        ...


@dataclass(frozen=True, slots=True)
class CliDependencies:
    resolve_paths: PathResolver
    initializer: RuntimeInitializer
    doctor_factory: DoctorFactory
    migration_factory: MigrationFactory
    runtime_control_factory: RuntimeControlFactory
    bootstrap_admin_factory: BootstrapAdminFactory


def _safe_error(
    message: str,
    *,
    code: int = 2,
    json_output: bool = False,
    command: str = "pangi",
) -> NoReturn:
    safe_message = redact_text(message)
    if json_output:
        typer.echo(
            render_json(
                {
                    "schema_version": 1,
                    "command": command,
                    "status": "ERROR",
                    "exit_code": code,
                    "error": {"message": safe_message},
                }
            )
        )
    else:
        typer.echo(f"Error: {safe_message}", err=True)
    raise typer.Exit(code)


def _plan_payload(plan: InitPlan) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command": "init",
        "status": "ERROR" if plan.conflicts else "planned",
        "exit_code": 1 if plan.conflicts else 0,
        "paths": plan.paths.as_dict(),
        "actions": [
            {"kind": action.kind.value, "path": str(action.path)} for action in plan.actions
        ],
        "conflicts": list(plan.conflicts),
    }


def _result_payload(
    result: InitResult,
    paths: RuntimePaths,
    bootstrap: BootstrapIssueResult,
) -> dict[str, object]:
    status = "initialized" if result.created or result.modified else "already_initialized"
    return {
        "schema_version": 1,
        "command": "init",
        "status": status,
        "paths": paths.as_dict(),
        "created": [str(path) for path in result.created],
        "modified": [str(path) for path in result.modified],
        "preserved": [str(path) for path in result.preserved],
        "bootstrap": bootstrap.as_dict(),
    }


def create_app(dependencies: CliDependencies) -> typer.Typer:
    """Build an explicit Typer application suitable for production and tests."""

    app = typer.Typer(
        name="pangi",
        help="Pangi agent runtime",
        no_args_is_help=True,
        invoke_without_command=True,
        add_completion=False,
        pretty_exceptions_enable=False,
    )
    config_app = typer.Typer(help="Inspect and validate configuration", add_completion=False)
    migrate_app = typer.Typer(help="Plan and apply SQLite migrations", add_completion=False)
    bootstrap_app = typer.Typer(help="Recover first-run Admin access", add_completion=False)

    @app.callback()
    def root(
        version_flag: Annotated[
            bool,
            typer.Option("--version", help="Show the Pangi version and exit", is_eager=True),
        ] = False,
    ) -> None:
        if version_flag:
            typer.echo(f"pangi {__version__}")
            raise typer.Exit()

    @app.command("version")
    def version_command(
        json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON")] = False,
    ) -> None:
        payload = {"schema_version": 1, "command": "version", "version": __version__}
        typer.echo(render_json(payload) if json_output else f"pangi {__version__}")

    @config_app.command("path")
    def config_path_command(
        project_local: Annotated[
            bool,
            typer.Option("--project-local", help="Resolve paths under .pangi"),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON")] = False,
    ) -> None:
        try:
            paths = dependencies.resolve_paths(project_local, None)
        except RuntimeError as error:
            _safe_error(str(error))
        payload = {"schema_version": 1, "command": "config.path", "paths": paths.as_dict()}
        if json_output:
            typer.echo(render_json(payload))
            return
        for name, value in paths.as_dict().items():
            typer.echo(f"{name}: {value}")

    @config_app.command("validate")
    def config_validate_command(
        path: Annotated[
            Path | None,
            typer.Option("--path", help="Validate this config instead of the resolved path"),
        ] = None,
        project_local: Annotated[bool, typer.Option("--project-local")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        try:
            paths = dependencies.resolve_paths(project_local, path)
            config = PangiConfig.load(paths.config_file)
        except (OSError, PangiConfigError, RuntimeError) as error:
            _safe_error(
                str(error),
                json_output=json_output,
                command="config.validate",
            )
        payload = {
            "schema_version": 1,
            "command": "config.validate",
            "status": "valid",
            "config_schema_version": config.schema_version,
            "path": str(paths.config_file),
        }
        typer.echo(render_json(payload) if json_output else f"Valid: {paths.config_file}")

    @app.command("init")
    def init_command(
        source_config: Annotated[
            Path | None,
            typer.Option("--config", help="Source TOML for the new installation"),
        ] = None,
        project_local: Annotated[bool, typer.Option("--project-local")] = False,
        non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
        assume_yes: Annotated[bool, typer.Option("--yes", help="Apply the displayed plan")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        if non_interactive and source_config is None:
            _safe_error(
                "--non-interactive requires --config",
                json_output=json_output,
                command="init",
            )
        if json_output and not (non_interactive or assume_yes):
            _safe_error(
                "--json requires --non-interactive or --yes",
                json_output=True,
                command="init",
            )
        try:
            config = PangiConfig.load(source_config) if source_config else PangiConfig()
            paths = dependencies.resolve_paths(project_local, None)
            plan = dependencies.initializer.plan(paths)
            preserves_config = any(
                action.kind is InitActionKind.PRESERVE_EXISTING and action.path == paths.config_file
                for action in plan.actions
            )
            if preserves_config:
                PangiConfig.load(paths.config_file)
        except (OSError, PangiConfigError, RuntimeError) as error:
            _safe_error(str(error), json_output=json_output, command="init")

        if plan.conflicts:
            if json_output:
                typer.echo(render_json(_plan_payload(plan)))
            else:
                typer.echo("Initialization conflicts:", err=True)
                for conflict in plan.conflicts:
                    typer.echo(f"- {redact_text(conflict)}", err=True)
            raise typer.Exit(1)

        if not json_output:
            typer.echo("Initialization plan:")
            for action in plan.actions:
                typer.echo(f"- {action.kind.value}: {action.path}")
        if not (non_interactive or assume_yes) and not typer.confirm("Apply this plan?"):
            typer.echo("Initialization cancelled")
            return

        try:
            result = dependencies.initializer.apply(plan, config.to_toml())
            installed_config = PangiConfig.load(paths.config_file)
            bootstrap = asyncio.run(
                dependencies.bootstrap_admin_factory(paths, installed_config).issue_url()
            )
        except (OSError, PangiConfigError, BootstrapOperationError, StorageOperationError) as error:
            _safe_error(
                str(error),
                code=1,
                json_output=json_output,
                command="init",
            )
        if json_output:
            typer.echo(render_json(_result_payload(result, paths, bootstrap)))
            return
        typer.echo(f"Initialized: {paths.root}")
        typer.echo(f"Config: {paths.config_file}")
        if bootstrap.bootstrap_url is not None and bootstrap.expires_at is not None:
            typer.echo(f"Bootstrap URL: {bootstrap.bootstrap_url}")
            typer.echo(f"Expires: {bootstrap.expires_at.isoformat()}")
        elif bootstrap.status is BootstrapIssueStatus.ADMIN_EXISTS:
            typer.echo("Bootstrap: Admin already configured")
        else:
            typer.echo(
                "Bootstrap: Grant already issued; use 'pangi bootstrap rotate --yes' to recover"
            )

    @bootstrap_app.command("rotate")
    def bootstrap_rotate_command(
        path: Annotated[Path | None, typer.Option("--config")] = None,
        project_local: Annotated[bool, typer.Option("--project-local")] = False,
        assume_yes: Annotated[
            bool,
            typer.Option("--yes", help="Revoke the previous Grant and issue a new one"),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        if not assume_yes:
            _safe_error(
                "bootstrap rotation requires --yes",
                json_output=json_output,
                command="bootstrap.rotate",
            )
        try:
            paths = dependencies.resolve_paths(project_local, path)
            config = PangiConfig.load(paths.config_file)
            result = asyncio.run(
                dependencies.bootstrap_admin_factory(paths, config).issue_url(rotate=True)
            )
        except (BootstrapOperationError, StorageOperationError, OSError) as error:
            _safe_error(
                str(error),
                code=1,
                json_output=json_output,
                command="bootstrap.rotate",
            )
        except (PangiConfigError, RuntimeError) as error:
            _safe_error(
                str(error),
                json_output=json_output,
                command="bootstrap.rotate",
            )
        if result.status is BootstrapIssueStatus.ADMIN_EXISTS:
            _safe_error(
                "Bootstrap is already configured",
                code=1,
                json_output=json_output,
                command="bootstrap.rotate",
            )
        if result.bootstrap_url is None or result.expires_at is None:
            _safe_error(
                "Bootstrap Grant could not be issued",
                code=1,
                json_output=json_output,
                command="bootstrap.rotate",
            )
        payload = {
            "schema_version": 1,
            "command": "bootstrap.rotate",
            **result.as_dict(),
        }
        if json_output:
            typer.echo(render_json(payload))
            return
        typer.echo(f"Bootstrap URL: {result.bootstrap_url}")
        typer.echo(f"Expires: {result.expires_at.isoformat()}")

    @app.command("doctor")
    def doctor_command(
        path: Annotated[Path | None, typer.Option("--config")] = None,
        project_local: Annotated[bool, typer.Option("--project-local")] = False,
        offline: Annotated[bool, typer.Option("--offline")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
        strict: Annotated[bool, typer.Option("--strict")] = False,
    ) -> None:
        try:
            paths = dependencies.resolve_paths(project_local, path)
            config = PangiConfig.load(paths.config_file)
        except (PangiConfigError, RuntimeError) as error:
            _safe_error(
                str(error),
                json_output=json_output,
                command="doctor",
            )
        report = dependencies.doctor_factory(paths, config).run(offline=offline)
        if json_output:
            typer.echo(render_json(report.as_dict(strict=strict)))
        else:
            typer.echo(f"Pangi Doctor {report.pangi_version}")
            for check in report.checks:
                summary = redact_text(check.summary)
                typer.echo(f"{check.status.value:<4}  {check.check_id:<24} {summary}")
            typer.echo(f"Exit code: {report.exit_code(strict=strict)}")
        raise typer.Exit(report.exit_code(strict=strict))

    @migrate_app.command("plan")
    def migrate_plan_command(
        path: Annotated[Path | None, typer.Option("--config")] = None,
        project_local: Annotated[bool, typer.Option("--project-local")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        try:
            paths = dependencies.resolve_paths(project_local, path)
            config = PangiConfig.load(paths.config_file)
            plan = asyncio.run(dependencies.migration_factory(paths, config).plan())
        except (PangiConfigError, RuntimeError) as error:
            if isinstance(error, StorageOperationError):
                _safe_error(
                    str(error),
                    code=1,
                    json_output=json_output,
                    command="migrate.plan",
                )
            _safe_error(str(error), json_output=json_output, command="migrate.plan")
        except OSError as error:
            _safe_error(
                str(error),
                code=1,
                json_output=json_output,
                command="migrate.plan",
            )
        payload = {
            "schema_version": 1,
            "command": "migrate.plan",
            "status": "pending" if plan.pending else "up_to_date",
            **plan.as_dict(),
        }
        if json_output:
            typer.echo(render_json(payload))
            return
        typer.echo(f"Database: {plan.database_file}")
        typer.echo(f"Current version: {plan.current_version}")
        typer.echo(f"Target version: {plan.target_version}")
        if plan.pending:
            typer.echo("Pending migrations:")
            for migration in plan.pending:
                typer.echo(f"- {migration.version:04d} {migration.name}")
        else:
            typer.echo("No pending migrations")

    @migrate_app.command("apply")
    def migrate_apply_command(
        path: Annotated[Path | None, typer.Option("--config")] = None,
        project_local: Annotated[bool, typer.Option("--project-local")] = False,
        assume_yes: Annotated[bool, typer.Option("--yes")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        if json_output and not assume_yes:
            _safe_error(
                "--json requires --yes",
                json_output=True,
                command="migrate.apply",
            )
        try:
            paths = dependencies.resolve_paths(project_local, path)
            config = PangiConfig.load(paths.config_file)
            admin = dependencies.migration_factory(paths, config)
            plan = asyncio.run(admin.plan())
        except (PangiConfigError, RuntimeError) as error:
            if isinstance(error, StorageOperationError):
                _safe_error(
                    str(error),
                    code=1,
                    json_output=json_output,
                    command="migrate.apply",
                )
            _safe_error(str(error), json_output=json_output, command="migrate.apply")
        except OSError as error:
            _safe_error(
                str(error),
                code=1,
                json_output=json_output,
                command="migrate.apply",
            )
        if plan.pending and not assume_yes:
            typer.echo(f"Pending migrations: {len(plan.pending)}")
            if plan.backup_required:
                typer.echo("A verified pre-migration backup will be created")
            if not typer.confirm("Apply this migration plan?"):
                typer.echo("Migration cancelled")
                return
        try:
            result = asyncio.run(admin.apply())
        except (OSError, StorageOperationError) as error:
            _safe_error(
                str(error),
                code=1,
                json_output=json_output,
                command="migrate.apply",
            )
        payload = {
            "schema_version": 1,
            "command": "migrate.apply",
            "status": "migrated" if result.applied else "up_to_date",
            **result.as_dict(),
        }
        if json_output:
            typer.echo(render_json(payload))
            return
        if result.applied:
            typer.echo(f"Applied {len(result.applied)} migration(s)")
            typer.echo(f"Current version: {result.current_version}")
            if result.backup_file is not None:
                typer.echo(f"Backup: {result.backup_file}")
        else:
            typer.echo("Database is already up to date")

    @app.command("start")
    def start_command(
        path: Annotated[Path | None, typer.Option("--config")] = None,
        project_local: Annotated[bool, typer.Option("--project-local")] = False,
    ) -> None:
        try:
            paths = dependencies.resolve_paths(project_local, path)
            config = PangiConfig.load(paths.config_file)
            dependencies.runtime_control_factory(paths, config).start()
        except PangiConfigError as error:
            _safe_error(str(error))
        except (RuntimeUnavailableError, RuntimeError) as error:
            _safe_error(str(error), code=1)

    @app.command("status")
    def status_command(
        path: Annotated[Path | None, typer.Option("--config")] = None,
        project_local: Annotated[bool, typer.Option("--project-local")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        try:
            paths = dependencies.resolve_paths(project_local, path)
            config = PangiConfig.load(paths.config_file)
            status = dependencies.runtime_control_factory(paths, config).status()
        except (PangiConfigError, RuntimeError) as error:
            _safe_error(
                str(error),
                json_output=json_output,
                command="status",
            )
        payload = {"schema_version": 1, "command": "status", **status.as_dict()}
        text = f"{status.state.value}: {redact_text(status.detail)}"
        typer.echo(render_json(payload) if json_output else text)
        if status.state is not RuntimeState.RUNNING:
            raise typer.Exit(1)

    app.add_typer(config_app, name="config")
    app.add_typer(migrate_app, name="migrate")
    app.add_typer(bootstrap_app, name="bootstrap")
    return app
