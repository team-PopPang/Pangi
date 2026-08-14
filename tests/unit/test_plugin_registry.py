"""Lazy plugin discovery tests."""

from importlib import metadata

import pytest

from pangi.plugins import ENTRY_POINT_GROUPS, PluginKind, PluginRegistry


def test_entry_point_groups_are_stable() -> None:
    assert dict(ENTRY_POINT_GROUPS) == {
        PluginKind.PROVIDER: "pangi.providers",
        PluginKind.CHANNEL: "pangi.channels",
        PluginKind.SECRET_STORE: "pangi.secret_stores",
        PluginKind.SUBAGENT: "pangi.subagents",
    }


def test_discovery_reads_metadata_without_loading_plugin() -> None:
    entry_point = metadata.EntryPoint(
        name="example",
        value="example_plugin:create",
        group="pangi.providers",
    )

    def provider(group: str) -> list[metadata.EntryPoint]:
        return [entry_point] if group == "pangi.providers" else []

    registry = PluginRegistry.discover(provider)

    descriptor = registry.find(PluginKind.PROVIDER, "example")
    assert descriptor is not None
    assert descriptor.entry_point.value == "example_plugin:create"


def test_uninstalled_plugins_produce_an_empty_registry() -> None:
    registry = PluginRegistry.discover(lambda group: [])

    assert tuple(registry) == ()


def test_duplicate_entry_point_names_are_rejected() -> None:
    first = metadata.EntryPoint(name="same", value="first:create", group="pangi.providers")
    second = metadata.EntryPoint(name="same", value="second:create", group="pangi.providers")

    def provider(group: str) -> list[metadata.EntryPoint]:
        return [first, second] if group == "pangi.providers" else []

    with pytest.raises(ValueError, match="duplicate"):
        PluginRegistry.discover(provider)
