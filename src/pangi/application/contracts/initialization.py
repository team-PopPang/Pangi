"""Initialization planning and result contracts."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pangi.application.contracts.paths import RuntimePaths


class InitActionKind(StrEnum):
    """Mutation or preservation represented in an initialization plan."""

    CREATE_DIRECTORY = "create_directory"
    CREATE_CONFIG = "create_config"
    UPDATE_GITIGNORE = "update_gitignore"
    PRESERVE_EXISTING = "preserve_existing"


@dataclass(frozen=True, slots=True)
class InitAction:
    kind: InitActionKind
    path: Path


@dataclass(frozen=True, slots=True)
class InitPlan:
    paths: RuntimePaths
    actions: tuple[InitAction, ...]
    conflicts: tuple[str, ...] = ()

    @property
    def can_apply(self) -> bool:
        return not self.conflicts


@dataclass(frozen=True, slots=True)
class InitResult:
    created: tuple[Path, ...]
    modified: tuple[Path, ...]
    preserved: tuple[Path, ...]

