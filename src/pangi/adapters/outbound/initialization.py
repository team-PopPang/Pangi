"""Fail-closed local filesystem initialization."""

from __future__ import annotations

import os
from pathlib import Path

from pangi.application.contracts.initialization import (
    InitAction,
    InitActionKind,
    InitPlan,
    InitResult,
)
from pangi.application.contracts.paths import RuntimePaths

GITIGNORE_START = "# >>> Pangi runtime >>>"
GITIGNORE_END = "# <<< Pangi runtime <<<"
GITIGNORE_BLOCK = "\n".join(
    (
        GITIGNORE_START,
        ".pangi/",
        "pangi-data/",
        "*.pangi.sqlite3",
        "*.pangi.sqlite3-*",
        GITIGNORE_END,
    )
)


class InitializationConflictError(RuntimeError):
    """Raised before unsafe or ambiguous filesystem mutations."""


class FileSystemInitializer:
    """Plan and apply idempotent runtime directory initialization."""

    def plan(self, paths: RuntimePaths) -> InitPlan:
        actions: list[InitAction] = []
        conflicts: list[str] = []
        directories = tuple(
            dict.fromkeys(
                (
                    paths.config_file.parent,
                    paths.data_dir,
                    paths.log_dir,
                    paths.backup_dir,
                    paths.vault_dir,
                )
            )
        )

        for directory in directories:
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                conflicts.append(f"directory target is unsafe: {directory}")
            elif directory.exists():
                actions.append(InitAction(InitActionKind.PRESERVE_EXISTING, directory))
            else:
                actions.append(InitAction(InitActionKind.CREATE_DIRECTORY, directory))

        config_path = paths.config_file
        if config_path.is_symlink() or (config_path.exists() and not config_path.is_file()):
            conflicts.append(f"configuration target is unsafe: {config_path}")
        elif config_path.exists():
            actions.append(InitAction(InitActionKind.PRESERVE_EXISTING, config_path))
        else:
            actions.append(InitAction(InitActionKind.CREATE_CONFIG, config_path))

        if paths.project_root is not None:
            gitignore_path = paths.project_root / ".gitignore"
            if gitignore_path.is_symlink() or (
                gitignore_path.exists() and not gitignore_path.is_file()
            ):
                conflicts.append(f"gitignore target is unsafe: {gitignore_path}")
            else:
                content = gitignore_path.read_text("utf-8") if gitignore_path.exists() else ""
                has_start = GITIGNORE_START in content
                has_end = GITIGNORE_END in content
                if has_start != has_end:
                    conflicts.append(f"incomplete Pangi marker block: {gitignore_path}")
                elif has_start:
                    start = content.index(GITIGNORE_START)
                    end = content.index(GITIGNORE_END, start) + len(GITIGNORE_END)
                    if content[start:end] != GITIGNORE_BLOCK:
                        conflicts.append(f"modified Pangi marker block: {gitignore_path}")
                    else:
                        actions.append(
                            InitAction(InitActionKind.PRESERVE_EXISTING, gitignore_path)
                        )
                else:
                    actions.append(InitAction(InitActionKind.UPDATE_GITIGNORE, gitignore_path))

        return InitPlan(paths=paths, actions=tuple(actions), conflicts=tuple(conflicts))

    def apply(self, plan: InitPlan, config_text: str) -> InitResult:
        if not plan.can_apply:
            raise InitializationConflictError("initialization plan contains unsafe conflicts")

        created: list[Path] = []
        modified: list[Path] = []
        preserved: list[Path] = []

        for action in plan.actions:
            if action.kind is InitActionKind.CREATE_DIRECTORY:
                if action.path.is_symlink():
                    raise InitializationConflictError(f"directory became unsafe: {action.path}")
                if action.path.exists():
                    if not action.path.is_dir():
                        message = f"directory target changed: {action.path}"
                        raise InitializationConflictError(message)
                    preserved.append(action.path)
                    continue
                action.path.mkdir(parents=True, mode=0o700)
                action.path.chmod(0o700)
                created.append(action.path)
            elif action.kind is InitActionKind.CREATE_CONFIG:
                if action.path.is_symlink():
                    raise InitializationConflictError(f"configuration became unsafe: {action.path}")
                try:
                    with action.path.open("x", encoding="utf-8") as config_file:
                        config_file.write(config_text)
                        config_file.flush()
                        os.fsync(config_file.fileno())
                    action.path.chmod(0o600)
                    created.append(action.path)
                except FileExistsError:
                    preserved.append(action.path)
            elif action.kind is InitActionKind.UPDATE_GITIGNORE:
                self._append_gitignore(action.path)
                modified.append(action.path)
            else:
                preserved.append(action.path)

        return InitResult(
            created=tuple(dict.fromkeys(created)),
            modified=tuple(dict.fromkeys(modified)),
            preserved=tuple(dict.fromkeys(preserved)),
        )

    @staticmethod
    def _append_gitignore(path: Path) -> None:
        if path.is_symlink():
            raise InitializationConflictError(f"gitignore became unsafe: {path}")
        existing = path.read_text("utf-8") if path.exists() else ""
        if GITIGNORE_START in existing and GITIGNORE_END in existing:
            start = existing.index(GITIGNORE_START)
            end = existing.index(GITIGNORE_END, start) + len(GITIGNORE_END)
            if existing[start:end] == GITIGNORE_BLOCK:
                return
            raise InitializationConflictError(f"modified Pangi marker block: {path}")
        if GITIGNORE_START in existing or GITIGNORE_END in existing:
            raise InitializationConflictError(f"incomplete Pangi marker block: {path}")
        separator = "" if not existing or existing.endswith("\n") else "\n"
        prefix = "" if not existing else "\n"
        with path.open("a", encoding="utf-8") as gitignore:
            gitignore.write(f"{separator}{prefix}{GITIGNORE_BLOCK}\n")
