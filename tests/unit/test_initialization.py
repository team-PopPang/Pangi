"""Fail-closed and idempotent initialization tests."""

import os
from pathlib import Path

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import (
    GITIGNORE_END,
    GITIGNORE_START,
    FileSystemInitializer,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths


def test_project_local_init_is_idempotent_and_preserves_config(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(
        project_local=True,
        project_root=tmp_path,
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    initializer = FileSystemInitializer()
    config_text = PangiConfig().to_toml()

    first = initializer.apply(initializer.plan(paths), config_text)
    original = paths.config_file.read_text("utf-8")
    paths.config_file.write_text(f"{original}\n# user edit\n", "utf-8")
    second = initializer.apply(initializer.plan(paths), PangiConfig().to_toml())

    assert paths.config_file in first.created
    assert paths.config_file in second.preserved
    assert paths.config_file.read_text("utf-8").endswith("# user edit\n")
    gitignore = (tmp_path / ".gitignore").read_text("utf-8")
    assert gitignore.count(GITIGNORE_START) == 1
    assert gitignore.count(GITIGNORE_END) == 1


def test_created_runtime_paths_use_private_permissions(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    initializer = FileSystemInitializer()

    initializer.apply(initializer.plan(paths), PangiConfig().to_toml())

    assert os.stat(paths.config_file).st_mode & 0o777 == 0o600
    for directory in (paths.data_dir, paths.log_dir, paths.backup_dir, paths.vault_dir):
        assert os.stat(directory).st_mode & 0o777 == 0o700


def test_symlinked_runtime_root_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    runtime_root = tmp_path / ".pangi"
    runtime_root.symlink_to(target, target_is_directory=True)
    paths = resolve_runtime_paths(
        project_local=True,
        project_root=tmp_path,
        environ={},
        platform="linux",
        user_home=tmp_path,
    )

    plan = FileSystemInitializer().plan(paths)

    assert plan.can_apply is False
    assert any("unsafe" in conflict for conflict in plan.conflicts)


def test_incomplete_gitignore_marker_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(f"{GITIGNORE_START}\n.pangi/\n", "utf-8")
    paths = resolve_runtime_paths(
        project_local=True,
        project_root=tmp_path,
        environ={},
        platform="linux",
        user_home=tmp_path,
    )

    plan = FileSystemInitializer().plan(paths)

    assert plan.can_apply is False
    assert any("incomplete" in conflict for conflict in plan.conflicts)


def test_modified_gitignore_marker_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        f"{GITIGNORE_START}\n# missing runtime patterns\n{GITIGNORE_END}\n",
        "utf-8",
    )
    paths = resolve_runtime_paths(
        project_local=True,
        project_root=tmp_path,
        environ={},
        platform="linux",
        user_home=tmp_path,
    )

    plan = FileSystemInitializer().plan(paths)

    assert plan.can_apply is False
    assert any("modified" in conflict for conflict in plan.conflicts)
