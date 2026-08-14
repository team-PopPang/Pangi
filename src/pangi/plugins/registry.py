"""Lazy Python entry point discovery for installable extensions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from importlib import metadata
from types import MappingProxyType
from typing import Protocol

from pangi.plugins.manifests import PluginKind, PluginManifest

ENTRY_POINT_GROUPS: Mapping[PluginKind, str] = MappingProxyType(
    {
        PluginKind.PROVIDER: "pangi.providers",
        PluginKind.CHANNEL: "pangi.channels",
        PluginKind.SECRET_STORE: "pangi.secret_stores",
        PluginKind.SUBAGENT: "pangi.subagents",
    }
)

EntryPointProvider = Callable[[str], Iterable[metadata.EntryPoint]]


class PluginFactory(Protocol):
    """Stable callable shape exposed by a plugin entry point."""

    manifest: PluginManifest

    def __call__(self) -> object:
        """Create the concrete plugin object."""

        ...


@dataclass(frozen=True, slots=True)
class DiscoveredPlugin:
    """An installed plugin descriptor whose implementation has not been imported."""

    name: str
    kind: PluginKind
    entry_point: metadata.EntryPoint

    def load(self) -> object:
        """Import the plugin only after the caller explicitly selects it."""

        return self.entry_point.load()


def _installed_entry_points(group: str) -> Iterable[metadata.EntryPoint]:
    return metadata.entry_points(group=group)


def discover_plugins(
    entry_point_provider: EntryPointProvider | None = None,
) -> tuple[DiscoveredPlugin, ...]:
    """Discover installed metadata without importing plugin implementations."""

    provider = entry_point_provider or _installed_entry_points
    discovered = (
        DiscoveredPlugin(name=entry_point.name, kind=kind, entry_point=entry_point)
        for kind, group in ENTRY_POINT_GROUPS.items()
        for entry_point in provider(group)
    )
    return tuple(sorted(discovered, key=lambda item: (item.kind.value, item.name)))


class PluginRegistry:
    """Immutable-key registry of lazily discovered plugin descriptors."""

    def __init__(self, descriptors: Iterable[DiscoveredPlugin] = ()) -> None:
        self._descriptors: dict[tuple[PluginKind, str], DiscoveredPlugin] = {}
        for descriptor in descriptors:
            key = (descriptor.kind, descriptor.name)
            if key in self._descriptors:
                message = f"duplicate plugin entry point: {descriptor.kind.value}/{descriptor.name}"
                raise ValueError(message)
            self._descriptors[key] = descriptor

    @classmethod
    def discover(cls, entry_point_provider: EntryPointProvider | None = None) -> PluginRegistry:
        """Create a registry from installed entry point metadata."""

        return cls(discover_plugins(entry_point_provider))

    def __iter__(self) -> Iterator[DiscoveredPlugin]:
        return iter(
            sorted(self._descriptors.values(), key=lambda item: (item.kind.value, item.name))
        )

    def find(self, kind: PluginKind, name: str) -> DiscoveredPlugin | None:
        """Return a descriptor without loading its implementation."""

        return self._descriptors.get((kind, name))
