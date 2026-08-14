"""Runtime filesystem path contracts."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class PathMode(StrEnum):
    """How runtime paths were selected."""

    OS_DEFAULT = "os_default"
    PANGI_HOME = "pangi_home"
    EXPLICIT = "explicit"
    PROJECT_LOCAL = "project_local"


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Resolved paths for configuration and mutable runtime data."""

    mode: PathMode
    root: Path
    config_file: Path
    data_dir: Path
    log_dir: Path
    backup_dir: Path
    vault_dir: Path
    project_root: Path | None = None

    def as_dict(self) -> dict[str, str]:
        """Return safe display values without reading directory contents."""

        return {
            "mode": self.mode.value,
            "root": str(self.root),
            "config": str(self.config_file),
            "data": str(self.data_dir),
            "logs": str(self.log_dir),
            "backups": str(self.backup_dir),
            "vault": str(self.vault_dir),
        }

