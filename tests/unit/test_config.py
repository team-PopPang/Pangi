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
