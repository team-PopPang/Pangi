"""Runtime path precedence and platform tests."""

from pathlib import Path

import pytest

from pangi.adapters.outbound.runtime_paths import (
    UnsupportedPlatformError,
    resolve_runtime_paths,
)
from pangi.application.contracts.paths import PathMode


def test_explicit_home_has_priority_over_environment(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit"
    paths = resolve_runtime_paths(
        explicit_home=explicit,
        environ={"PANGI_HOME": str(tmp_path / "environment")},
        platform="linux",
        user_home=tmp_path,
    )

    assert paths.mode is PathMode.EXPLICIT
    assert paths.config_file == explicit / "pangi.toml"
    assert paths.data_dir == explicit / "data"


def test_pangi_home_precedes_os_defaults(tmp_path: Path) -> None:
    root = tmp_path / "pangi-home"
    paths = resolve_runtime_paths(
        environ={"PANGI_HOME": str(root)},
        platform="linux",
        user_home=tmp_path,
    )

    assert paths.mode is PathMode.PANGI_HOME
    assert paths.root == root


def test_linux_uses_xdg_directories(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(
        environ={
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
        },
        platform="linux",
        user_home=tmp_path / "home",
    )

    assert paths.config_file == tmp_path / "config" / "pangi" / "pangi.toml"
    assert paths.data_dir == tmp_path / "data" / "pangi"
    assert paths.log_dir == tmp_path / "state" / "pangi" / "logs"


def test_macos_uses_application_support_and_logs(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(environ={}, platform="darwin", user_home=tmp_path)

    expected_config = tmp_path / "Library" / "Application Support" / "Pangi" / "pangi.toml"
    assert paths.config_file == expected_config
    assert paths.log_dir == tmp_path / "Library" / "Logs" / "Pangi"


def test_project_local_mode_is_explicit_and_self_contained(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(
        project_local=True,
        project_root=tmp_path,
        environ={"PANGI_HOME": str(tmp_path / "ignored")},
        platform="linux",
        user_home=tmp_path,
    )

    assert paths.mode is PathMode.PROJECT_LOCAL
    assert paths.root == tmp_path / ".pangi"
    assert paths.project_root == tmp_path


def test_config_file_can_be_overridden_without_moving_data(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "home",
        explicit_config=tmp_path / "custom.toml",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )

    assert paths.config_file == tmp_path / "custom.toml"
    assert paths.data_dir == tmp_path / "home" / "data"


def test_explicit_config_can_override_project_local_config(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(
        explicit_config=tmp_path / "custom.toml",
        project_local=True,
        project_root=tmp_path,
        environ={},
        platform="linux",
        user_home=tmp_path,
    )

    assert paths.config_file == tmp_path / "custom.toml"
    assert paths.data_dir == tmp_path / ".pangi" / "data"


def test_native_windows_is_rejected() -> None:
    with pytest.raises(UnsupportedPlatformError, match="WSL2"):
        resolve_runtime_paths(environ={}, platform="win32", user_home=Path("/tmp"))
