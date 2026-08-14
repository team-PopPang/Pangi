"""OS-aware runtime path resolution."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from pangi.application.contracts.paths import PathMode, RuntimePaths


class UnsupportedPlatformError(RuntimeError):
    """Raised when native execution is not part of the supported platform set."""


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser().absolute()


def _absolute_environment_path(
    environ: Mapping[str, str],
    name: str,
    fallback: Path,
) -> Path:
    value = environ.get(name)
    if not value:
        return fallback
    candidate = Path(value).expanduser()
    return candidate.absolute() if candidate.is_absolute() else fallback


def _paths_under_root(root: Path, mode: PathMode) -> RuntimePaths:
    data_dir = root / "data"
    return RuntimePaths(
        mode=mode,
        root=root,
        config_file=root / "pangi.toml",
        data_dir=data_dir,
        log_dir=root / "logs",
        backup_dir=root / "backups",
        vault_dir=root / "vault",
        database_file=data_dir / "pangi.sqlite3",
        process_lock_file=data_dir / "pangi.lock",
    )


def resolve_runtime_paths(
    *,
    explicit_home: str | Path | None = None,
    explicit_config: str | Path | None = None,
    project_local: bool = False,
    project_root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    user_home: str | Path | None = None,
) -> RuntimePaths:
    """Resolve mutable paths without creating or reading their contents."""

    environment = os.environ if environ is None else environ
    current_platform = sys.platform if platform is None else platform
    home = _expand(Path.home() if user_home is None else user_home)

    if project_local:
        project = _expand(Path.cwd() if project_root is None else project_root)
        paths = _paths_under_root(project / ".pangi", PathMode.PROJECT_LOCAL)
        paths = replace(paths, project_root=project)
        if explicit_config is not None:
            paths = replace(paths, config_file=_expand(explicit_config))
        return paths

    if explicit_home is not None:
        paths = _paths_under_root(_expand(explicit_home), PathMode.EXPLICIT)
    elif environment.get("PANGI_HOME"):
        paths = _paths_under_root(_expand(environment["PANGI_HOME"]), PathMode.PANGI_HOME)
    elif current_platform.startswith("linux"):
        config_root = _absolute_environment_path(
            environment,
            "XDG_CONFIG_HOME",
            home / ".config",
        ) / "pangi"
        data_root = _absolute_environment_path(
            environment,
            "XDG_DATA_HOME",
            home / ".local" / "share",
        ) / "pangi"
        state_root = _absolute_environment_path(
            environment,
            "XDG_STATE_HOME",
            home / ".local" / "state",
        ) / "pangi"
        paths = RuntimePaths(
            mode=PathMode.OS_DEFAULT,
            root=data_root,
            config_file=config_root / "pangi.toml",
            data_dir=data_root,
            log_dir=state_root / "logs",
            backup_dir=data_root / "backups",
            vault_dir=data_root / "vault",
            database_file=data_root / "pangi.sqlite3",
            process_lock_file=data_root / "pangi.lock",
        )
    elif current_platform == "darwin":
        application_support = home / "Library" / "Application Support" / "Pangi"
        paths = RuntimePaths(
            mode=PathMode.OS_DEFAULT,
            root=application_support,
            config_file=application_support / "pangi.toml",
            data_dir=application_support / "data",
            log_dir=home / "Library" / "Logs" / "Pangi",
            backup_dir=application_support / "backups",
            vault_dir=application_support / "vault",
            database_file=application_support / "data" / "pangi.sqlite3",
            process_lock_file=application_support / "data" / "pangi.lock",
        )
    else:
        raise UnsupportedPlatformError(
            "native Windows is not supported; use Linux, macOS, WSL2, or a container"
        )

    config_override = explicit_config or environment.get("PANGI_CONFIG")
    if config_override is None:
        return paths
    return RuntimePaths(
        mode=paths.mode,
        root=paths.root,
        config_file=_expand(config_override),
        data_dir=paths.data_dir,
        log_dir=paths.log_dir,
        backup_dir=paths.backup_dir,
        vault_dir=paths.vault_dir,
        database_file=paths.database_file,
        process_lock_file=paths.process_lock_file,
        project_root=paths.project_root,
    )
