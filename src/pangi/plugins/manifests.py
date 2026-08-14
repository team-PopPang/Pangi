"""Versioned metadata shared by built-ins and installable extensions."""

from dataclasses import dataclass
from enum import StrEnum


class PluginKind(StrEnum):
    """Supported extension categories and their stable manifest values."""

    PROVIDER = "provider"
    CHANNEL = "channel"
    SECRET_STORE = "secret_store"
    SUBAGENT = "subagent"


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Metadata validated before an extension becomes available to the runtime."""

    name: str
    version: str
    kind: PluginKind
    api_version: int
    requires_core: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PluginKind):
            raise ValueError("manifest kind must be a supported PluginKind")
        if not self.name or self.name != self.name.strip():
            raise ValueError("manifest name must be a non-empty trimmed value")
        if not self.version or self.version != self.version.strip():
            raise ValueError("manifest version must be a non-empty trimmed value")
        if self.api_version < 1:
            raise ValueError("manifest api_version must be at least 1")
        if not self.requires_core or self.requires_core != self.requires_core.strip():
            raise ValueError("manifest requires_core must be a non-empty trimmed value")
