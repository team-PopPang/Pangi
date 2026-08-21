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
    assert loaded.auth.bootstrap_grant_ttl_minutes == 30
    assert loaded.auth.session_ttl_minutes == 720
    assert loaded.auth.session_rotation_minutes == 30
    assert loaded.auth.login_attempt_limit == 5
    assert loaded.auth.login_attempt_window_seconds == 300
    assert loaded.model.root_profile == "root-default"
    assert loaded.model.max_attempts == 3
    assert loaded.model.retry_backoff_seconds == (0.5, 1.0)
    assert "api_key" not in config_path.read_text("utf-8")


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
