"""Capability Pack manifest registration boundary."""

from collections.abc import Iterator
from typing import Protocol

from pangi._version import __version__
from pangi.plugins.manifests import PluginManifest


class CapabilityPackCompatibility(Protocol):
    """Policy supplied by the composition root to interpret version constraints."""

    def validate(self, manifest: PluginManifest, *, core_version: str) -> None:
        """Raise an error when the manifest is incompatible with the core."""

        ...


class CapabilityPackRegistry:
    """Register only manifests accepted by the configured compatibility policy."""

    def __init__(
        self,
        compatibility: CapabilityPackCompatibility,
        *,
        core_version: str = __version__,
    ) -> None:
        self._compatibility = compatibility
        self._core_version = core_version
        self._manifests: dict[str, PluginManifest] = {}

    def register(self, manifest: PluginManifest) -> None:
        """Validate compatibility before mutating the registry."""

        self._compatibility.validate(manifest, core_version=self._core_version)
        if manifest.name in self._manifests:
            raise ValueError(f"duplicate capability pack manifest: {manifest.name}")
        self._manifests[manifest.name] = manifest

    def __iter__(self) -> Iterator[PluginManifest]:
        return iter(sorted(self._manifests.values(), key=lambda item: item.name))

