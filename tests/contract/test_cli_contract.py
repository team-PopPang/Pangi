"""Stable CLI command and JSON contract tests."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from typer.testing import CliRunner

from pangi import PangiConfig
from pangi.adapters.inbound.cli import CliDependencies, create_app
from pangi.adapters.outbound.initialization import GITIGNORE_START, FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.factory import (
    build_bootstrap_admin_for_cli,
    build_migration_admin,
)
from pangi.adapters.outbound.runtime_control import UnavailableRuntimeControl
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.adapters.outbound.system_checks import build_doctor_service


def _app(tmp_path: Path):
    def resolver(project_local: bool, config_path: Path | None):
        return resolve_runtime_paths(
            explicit_home=None if project_local else tmp_path / "runtime",
            explicit_config=config_path,
            project_local=project_local,
            project_root=tmp_path,
            environ={},
            platform="linux",
            user_home=tmp_path,
        )

    return create_app(
        CliDependencies(
            resolve_paths=resolver,
            initializer=FileSystemInitializer(),
            doctor_factory=build_doctor_service,
            migration_factory=build_migration_admin,
            runtime_control_factory=lambda _paths, _config: UnavailableRuntimeControl(),
            bootstrap_admin_factory=build_bootstrap_admin_for_cli,
        )
    )


def test_non_interactive_init_requires_explicit_config(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        _app(tmp_path),
        ["init", "--non-interactive", "--json"],
    )

    assert result.exit_code == 2
    assert json.loads(result.output)["exit_code"] == 2
    assert "requires --config" in result.output
    assert "Traceback" not in result.output


def test_project_local_init_and_validation_are_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "install.toml"
    source.write_text(PangiConfig().to_toml(), "utf-8")
    runner = CliRunner()
    app = _app(tmp_path)
    command = [
        "init",
        "--config",
        str(source),
        "--non-interactive",
        "--project-local",
        "--json",
    ]

    first = runner.invoke(app, command)
    second = runner.invoke(app, command)
    validation = runner.invoke(
        app,
        ["config", "validate", "--project-local", "--json"],
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert json.loads(second.output)["status"] == "already_initialized"
    assert validation.exit_code == 0
    assert json.loads(first.output)["schema_version"] == 1
    assert json.loads(validation.output)["status"] == "valid"
    assert (tmp_path / ".gitignore").read_text("utf-8").count(GITIGNORE_START) == 1


def test_init_rejects_an_invalid_existing_config_without_overwriting_it(tmp_path: Path) -> None:
    source = tmp_path / "install.toml"
    source.write_text(PangiConfig().to_toml(), "utf-8")
    runner = CliRunner()
    app = _app(tmp_path)
    command = ["init", "--config", str(source), "--non-interactive", "--json"]
    assert runner.invoke(app, command).exit_code == 0
    target = tmp_path / "runtime" / "pangi.toml"
    target.write_text("unknown = 'preserve-me'\n", "utf-8")

    result = runner.invoke(app, command)

    assert result.exit_code == 2
    assert "preserve-me" not in result.output
    assert target.read_text("utf-8") == "unknown = 'preserve-me'\n"


def test_doctor_offline_json_schema_and_exit_code(tmp_path: Path) -> None:
    source = tmp_path / "install.toml"
    source.write_text(PangiConfig().to_toml(), "utf-8")
    runner = CliRunner()
    app = _app(tmp_path)
    runner.invoke(
        app,
        ["init", "--config", str(source), "--non-interactive", "--json"],
    )
    migration = runner.invoke(app, ["migrate", "apply", "--yes", "--json"])

    result = runner.invoke(app, ["doctor", "--offline", "--json"])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert migration.exit_code == 0
    assert payload["schema_version"] == 1
    assert payload["exit_code"] == 0
    assert {check["status"] for check in payload["checks"]} <= {
        "PASS",
        "WARN",
        "FAIL",
        "SKIP",
    }
    assert all(check["status"] != "FAIL" for check in payload["checks"])


def test_doctor_json_returns_code_two_when_config_is_missing(tmp_path: Path) -> None:
    result = CliRunner().invoke(_app(tmp_path), ["doctor", "--offline", "--json"])
    payload = json.loads(result.output)

    assert result.exit_code == 2
    assert payload["schema_version"] == 1
    assert payload["status"] == "ERROR"
    assert payload["exit_code"] == 2
    assert "Traceback" not in result.output


def test_init_applies_sqlite_migrations_for_bootstrap(tmp_path: Path) -> None:
    source = tmp_path / "install.toml"
    source.write_text(PangiConfig().to_toml(), "utf-8")
    runner = CliRunner()
    app = _app(tmp_path)
    runner.invoke(
        app,
        ["init", "--config", str(source), "--non-interactive", "--json"],
    )

    result = runner.invoke(app, ["doctor", "--offline", "--json"])
    payload = json.loads(result.output)
    migration = next(check for check in payload["checks"] if check["id"] == "sqlite.migrations")

    assert result.exit_code == 0
    assert migration["status"] == "PASS"
    assert migration["next_command"] is None


def test_start_and_status_fail_explicitly_until_runtime_is_composed(tmp_path: Path) -> None:
    source = tmp_path / "install.toml"
    source.write_text(PangiConfig().to_toml(), "utf-8")
    runner = CliRunner()
    app = _app(tmp_path)
    runner.invoke(
        app,
        ["init", "--config", str(source), "--non-interactive", "--json"],
    )

    start = runner.invoke(app, ["start"])
    status = runner.invoke(app, ["status", "--json"])

    assert start.exit_code == 1
    assert "WBS 03 and WBS 04" in start.output
    assert "Traceback" not in start.output
    assert status.exit_code == 1
    assert json.loads(status.output)["state"] == "unavailable"


def test_status_returns_code_two_for_invalid_server_config(tmp_path: Path) -> None:
    source = tmp_path / "install.toml"
    source.write_text(PangiConfig().to_toml(), "utf-8")
    runner = CliRunner()
    app = _app(tmp_path)
    runner.invoke(app, ["init", "--config", str(source), "--non-interactive", "--json"])
    target = tmp_path / "runtime" / "pangi.toml"
    target.write_text(target.read_text("utf-8").replace("127.0.0.1", "host/path"), "utf-8")

    status = runner.invoke(app, ["status", "--json"])

    assert status.exit_code == 2
    assert json.loads(status.output)["exit_code"] == 2
    assert "Traceback" not in status.output


def test_migrate_plan_apply_and_repeat_have_stable_json(tmp_path: Path) -> None:
    source = tmp_path / "install.toml"
    source.write_text(PangiConfig().to_toml(), "utf-8")
    runner = CliRunner()
    app = _app(tmp_path)
    runner.invoke(
        app,
        ["init", "--config", str(source), "--non-interactive", "--json"],
    )

    plan = runner.invoke(app, ["migrate", "plan", "--json"])
    first = runner.invoke(app, ["migrate", "apply", "--yes", "--json"])
    second = runner.invoke(app, ["migrate", "apply", "--yes", "--json"])

    assert plan.exit_code == 0
    assert json.loads(plan.output)["status"] == "up_to_date"
    assert json.loads(plan.output)["current_version"] == 9
    assert json.loads(plan.output)["target_version"] == 9
    assert first.exit_code == 0
    assert json.loads(first.output)["status"] == "up_to_date"
    assert json.loads(first.output)["current_version"] == 9
    assert second.exit_code == 0
    assert json.loads(second.output)["status"] == "up_to_date"
    assert json.loads(second.output)["applied"] == []


def test_migrate_apply_json_requires_explicit_yes(tmp_path: Path) -> None:
    source = tmp_path / "install.toml"
    source.write_text(PangiConfig().to_toml(), "utf-8")
    runner = CliRunner()
    app = _app(tmp_path)
    runner.invoke(
        app,
        ["init", "--config", str(source), "--non-interactive", "--json"],
    )

    result = runner.invoke(app, ["migrate", "apply", "--json"])

    assert result.exit_code == 2
    assert json.loads(result.output)["exit_code"] == 2
    assert "requires --yes" in result.output


def test_init_issues_bootstrap_url_once_and_rotation_requires_yes(tmp_path: Path) -> None:
    source = tmp_path / "install.toml"
    source.write_text(PangiConfig().to_toml(), "utf-8")
    runner = CliRunner()
    app = _app(tmp_path)
    init = ["init", "--config", str(source), "--non-interactive", "--json"]

    first = runner.invoke(app, init)
    second = runner.invoke(app, init)
    refused = runner.invoke(app, ["bootstrap", "rotate", "--json"])
    rotated = runner.invoke(app, ["bootstrap", "rotate", "--yes", "--json"])

    first_payload = json.loads(first.output)
    second_payload = json.loads(second.output)
    rotated_payload = json.loads(rotated.output)
    first_url = first_payload["bootstrap"]["url"]
    rotated_url = rotated_payload["url"]
    assert first.exit_code == 0
    assert first_payload["bootstrap"]["status"] == "issued"
    assert urlsplit(first_url).fragment
    assert second.exit_code == 0
    assert second_payload["bootstrap"] == {"status": "already_issued"}
    assert refused.exit_code == 2
    assert json.loads(refused.output)["exit_code"] == 2
    assert rotated.exit_code == 0
    assert rotated_payload["status"] == "issued"
    assert rotated_url != first_url


def test_bootstrap_rotation_missing_config_uses_usage_exit_code(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        _app(tmp_path),
        ["bootstrap", "rotate", "--yes", "--json"],
    )

    assert result.exit_code == 2
    assert json.loads(result.output)["exit_code"] == 2
    assert "Traceback" not in result.output
