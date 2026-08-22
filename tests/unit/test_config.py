"""Typed TOML configuration tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from pangi import PangiConfig
from pangi.config import PangiConfigError


def test_default_config_round_trips_through_toml(tmp_path: Path) -> None:
    config = PangiConfig()
    config_path = tmp_path / "pangi.toml"
    config_path.write_text(config.to_toml(), "utf-8")

    loaded = PangiConfig.load(config_path)

    assert loaded == config
    assert loaded.server.host == "127.0.0.1"
    assert loaded.server.port == 8787
    assert loaded.storage.url == "sqlite:///{data_dir}/pangi.sqlite3"
    assert loaded.storage.journal_mode == "delete"
    assert loaded.storage.busy_timeout_ms == 5000
    assert loaded.secrets.backend == "auto"
    assert loaded.secrets.master_key_source == "environment"
    assert loaded.secrets.master_key_environment_variable == "PANGI_SECRET_MASTER_KEY"
    assert loaded.secrets.master_key_file is None
    assert loaded.mcp.stdio.allowed_executables == ()
    assert loaded.mcp.stdio.executable_aliases == {}
    assert loaded.mcp.stdio.environment_allowlist == ()
    assert loaded.auth.bootstrap_grant_ttl_minutes == 30
    assert loaded.auth.session_ttl_minutes == 720
    assert loaded.auth.session_rotation_minutes == 30
    assert loaded.auth.login_attempt_limit == 5
    assert loaded.auth.login_attempt_window_seconds == 300
    assert loaded.runtime.run_data_classes == ("restricted",)
    assert loaded.model.root_profile == "root-default"
    assert loaded.model.max_attempts == 3
    assert loaded.model.retry_backoff_seconds == (0.5, 1.0)
    assert "api_key" not in config_path.read_text("utf-8")
    assert "PANGI_SECRET_MASTER_KEY" in config_path.read_text("utf-8")


def test_secrets_section_is_optional_for_existing_schema_v1_config(tmp_path: Path) -> None:
    config_path = tmp_path / "legacy.toml"
    secrets_block = "\n".join(
        (
            "",
            "[secrets]",
            'backend = "auto"',
            'master_key_source = "environment"',
            'master_key_environment_variable = "PANGI_SECRET_MASTER_KEY"',
            "",
        )
    )
    config_path.write_text(
        PangiConfig().to_toml().replace(secrets_block, "\n"),
        "utf-8",
    )

    loaded = PangiConfig.load(config_path)

    assert loaded.secrets.backend == "auto"
    assert loaded.secrets.master_key_source == "environment"


def test_secret_store_config_renders_only_external_key_location(tmp_path: Path) -> None:
    master_key_file = tmp_path / "master.key"
    config = PangiConfig.model_validate(
        {
            "secrets": {
                "backend": "file-vault",
                "master_key_source": "file",
                "master_key_file": str(master_key_file),
            }
        }
    )

    rendered = config.to_toml()

    assert f'master_key_file = "{master_key_file}"' in rendered
    assert "master_key =" not in rendered


def test_mcp_section_is_optional_and_defaults_to_fail_closed(tmp_path: Path) -> None:
    config_path = tmp_path / "legacy.toml"
    mcp_block = "\n".join(
        (
            "",
            "[mcp.stdio]",
            "allowed_executables = []",
            "environment_allowlist = []",
            "",
            "[mcp.stdio.executable_aliases]",
            "",
        )
    )
    config_path.write_text(PangiConfig().to_toml().replace(mcp_block, "\n"), "utf-8")

    loaded = PangiConfig.load(config_path)

    assert loaded.mcp.stdio.allowed_executables == ()
    assert loaded.mcp.stdio.executable_aliases == {}
    assert loaded.mcp.stdio.environment_allowlist == ()


def test_stdio_mcp_policy_round_trips_registered_paths_and_aliases(tmp_path: Path) -> None:
    server = tmp_path / "filesystem-mcp"
    config = PangiConfig.model_validate(
        {
            "mcp": {
                "stdio": {
                    "allowed_executables": [str(server)],
                    "executable_aliases": {"filesystem": str(server)},
                    "environment_allowlist": ["FILESYSTEM_TOKEN"],
                }
            }
        }
    )
    config_path = tmp_path / "pangi.toml"
    config_path.write_text(config.to_toml(), "utf-8")

    loaded = PangiConfig.load(config_path)

    assert loaded == config
    rendered = config_path.read_text("utf-8")
    assert f'allowed_executables = ["{server}"]' in rendered
    assert f'"filesystem" = "{server}"' in rendered
    assert 'environment_allowlist = ["FILESYSTEM_TOKEN"]' in rendered


@pytest.mark.parametrize(
    "stdio",
    [
        {"allowed_executables": ["relative/server"]},
        {"allowed_executables": ["/opt/mcp", "/opt/mcp"]},
        {"executable_aliases": {"bad/alias": "/opt/mcp"}},
        {"executable_aliases": {"server": "relative/server"}},
        {"environment_allowlist": ["lowercase"]},
        {"environment_allowlist": ["PATH"]},
        {"environment_allowlist": ["LD_PRELOAD"]},
        {"environment_allowlist": ["DYLD_INSERT_LIBRARIES"]},
        {"environment_allowlist": ["TOKEN", "TOKEN"]},
    ],
)
def test_invalid_stdio_mcp_policy_is_rejected(stdio: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PangiConfig.model_validate({"mcp": {"stdio": stdio}})


@pytest.mark.parametrize(
    "secrets",
    [
        {"backend": "unknown"},
        {"master_key_environment_variable": "unsafe-name"},
        {"master_key_source": "file"},
        {"master_key_source": "file", "master_key_file": "relative.key"},
        {"master_key_source": "environment", "master_key_file": "/tmp/key"},
    ],
)
def test_invalid_secret_store_configuration_is_rejected(
    secrets: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PangiConfig.model_validate({"secrets": secrets})


def test_auth_section_is_optional_for_existing_schema_v1_config(tmp_path: Path) -> None:
    config_path = tmp_path / "legacy.toml"
    auth_block = "\n".join(
        (
            "",
            "[auth]",
            "bootstrap_grant_ttl_minutes = 30",
            "session_ttl_minutes = 720",
            "session_rotation_minutes = 30",
            "login_attempt_limit = 5",
            "login_attempt_window_seconds = 300",
            "",
        )
    )
    config_path.write_text(
        PangiConfig().to_toml().replace(auth_block, "\n"),
        "utf-8",
    )

    loaded = PangiConfig.load(config_path)

    assert loaded.auth.bootstrap_grant_ttl_minutes == 30
    assert loaded.auth.session_ttl_minutes == 720
    assert loaded.auth.session_rotation_minutes == 30
    assert loaded.auth.login_attempt_limit == 5
    assert loaded.auth.login_attempt_window_seconds == 300


def test_model_section_is_optional_for_existing_schema_v1_config(tmp_path: Path) -> None:
    config_path = tmp_path / "legacy.toml"
    model_block = "\n".join(
        (
            "",
            "[model]",
            'root_profile = "root-default"',
            "max_attempts = 3",
            "attempt_timeout_seconds = 30.0",
            "total_timeout_seconds = 90.0",
            "retry_backoff_seconds = [0.5, 1.0]",
            "",
        )
    )
    config_path.write_text(
        PangiConfig().to_toml().replace(model_block, "\n"),
        "utf-8",
    )

    loaded = PangiConfig.load(config_path)

    assert loaded.model.root_profile == "root-default"
    assert loaded.model.max_attempts == 3
    assert loaded.model.retry_backoff_seconds == (0.5, 1.0)


def test_run_data_classes_are_optional_for_existing_schema_v1_config(tmp_path: Path) -> None:
    config_path = tmp_path / "legacy.toml"
    config_path.write_text(
        PangiConfig().to_toml().replace('run_data_classes = ["restricted"]\n', ""),
        "utf-8",
    )

    loaded = PangiConfig.load(config_path)

    assert loaded.runtime.run_data_classes == ("restricted",)


@pytest.mark.parametrize("values", [[], ["internal", "internal"], ["secret"]])
def test_invalid_run_data_classes_are_rejected(values: list[str]) -> None:
    with pytest.raises(ValidationError):
        PangiConfig.model_validate({"runtime": {"run_data_classes": values}})


def test_unknown_keys_are_rejected_without_echoing_values(tmp_path: Path) -> None:
    config_path = tmp_path / "pangi.toml"
    config_path.write_text(
        """
schema_version = 1
unknown_secret = "sk-this-must-not-appear"
""".lstrip(),
        "utf-8",
    )

    with pytest.raises(PangiConfigError) as captured:
        PangiConfig.load(config_path)

    assert "unknown_secret" in str(captured.value)
    assert "sk-this-must-not-appear" not in str(captured.value)


def test_invalid_timezone_and_port_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PangiConfig.model_validate(
            {
                "schema_version": 1,
                "instance": {"timezone": "Not/A-Timezone"},
                "server": {"port": 70000},
            }
        )


@pytest.mark.parametrize(
    "model",
    [
        {"root_profile": "root profile"},
        {"max_attempts": 3, "retry_backoff_seconds": (0.1,)},
        {"attempt_timeout_seconds": 91.0, "total_timeout_seconds": 90.0},
        {"retry_backoff_seconds": (0.1, float("inf"))},
    ],
)
def test_invalid_model_runtime_policy_is_rejected(model: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PangiConfig.model_validate({"model": model})


@pytest.mark.parametrize("host", ["https://example.com", "host/path", "bad host"])
def test_invalid_server_host_is_rejected(host: str) -> None:
    with pytest.raises(ValidationError, match="IP address or DNS hostname"):
        PangiConfig.model_validate({"server": {"host": host}})


def test_missing_config_raises_safe_error(tmp_path: Path) -> None:
    with pytest.raises(PangiConfigError, match="configuration file not found"):
        PangiConfig.load(tmp_path / "missing.toml")


def test_unsupported_storage_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PangiConfig.model_validate(
            {
                "storage": {
                    "url": "postgresql://db.example/pangi",
                    "journal_mode": "wal",
                }
            }
        )
