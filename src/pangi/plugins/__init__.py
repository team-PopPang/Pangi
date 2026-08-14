"""Versioned discovery and registration boundaries for Pangi extensions."""

from pangi.plugins.capability_packs import (
    CapabilityPackCompatibility,
    CapabilityPackRegistry,
)
from pangi.plugins.manifests import PluginKind, PluginManifest
from pangi.plugins.registry import (
    ENTRY_POINT_GROUPS,
    DiscoveredPlugin,
    PluginFactory,
    PluginRegistry,
)

__all__ = (
    "ENTRY_POINT_GROUPS",
    "CapabilityPackCompatibility",
    "CapabilityPackRegistry",
    "DiscoveredPlugin",
    "PluginFactory",
    "PluginKind",
    "PluginManifest",
    "PluginRegistry",
)

