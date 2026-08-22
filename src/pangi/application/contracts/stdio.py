"""Secret-safe contracts for resolving a future stdio MCP launch."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
EXECUTABLE_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
RESERVED_ENVIRONMENT_NAMES = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "NODE_OPTIONS",
        "PATH",
        "PYTHONHOME",
        "PYTHONPATH",
    }
)


def is_reserved_environment_name(name: str) -> bool:
    """Return whether explicit child-process injection could alter execution semantics."""

    return (
        name in RESERVED_ENVIRONMENT_NAMES
        or name.startswith("LD_")
        or name.startswith("DYLD_")
    )


class StdioLaunchError(RuntimeError):
    """Base class for stdio policy failures that never echo launch input."""

    code = "stdio_launch_failed"
    safe_message = "stdio launch policy validation failed"

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class StdioConnectionNotSupportedError(StdioLaunchError):
    code = "stdio_connection_not_supported"
    safe_message = "Connection is not eligible for stdio launch"


class StdioExecutableNotAllowedError(StdioLaunchError):
    code = "stdio_executable_not_allowed"
    safe_message = "stdio executable is not allowed"


class StdioExecutableUnsafeError(StdioLaunchError):
    code = "stdio_executable_unsafe"
    safe_message = "stdio executable failed security validation"


class StdioEnvironmentNotAllowedError(StdioLaunchError):
    code = "stdio_environment_not_allowed"
    safe_message = "stdio environment is not allowed"


class StdioEnvironmentSecretError(StdioLaunchError):
    code = "stdio_environment_secret_failed"
    safe_message = "stdio environment Secret resolution failed"


class StdioWorkingDirectoryUnsafeError(StdioLaunchError):
    code = "stdio_working_directory_unsafe"
    safe_message = "stdio working directory failed security validation"


@dataclass(frozen=True, slots=True)
class ResolvedStdioLaunch:
    """Validated launch material with filesystem identities for a final pre-spawn check."""

    executable: Path = field(repr=False)
    args: tuple[str, ...] = field(repr=False)
    environment: Mapping[str, str] = field(repr=False)
    working_directory: Path = field(repr=False)
    executable_device: int
    executable_inode: int
    working_directory_device: int
    working_directory_inode: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "environment",
            MappingProxyType(dict(sorted(self.environment.items()))),
        )
