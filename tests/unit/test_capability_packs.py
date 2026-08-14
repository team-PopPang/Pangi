"""Capability Pack registration contract tests."""

import pytest

from pangi.plugins import CapabilityPackRegistry, PluginKind, PluginManifest


class RecordingCompatibility:
    def __init__(self) -> None:
        self.calls: list[tuple[PluginManifest, str]] = []

    def validate(self, manifest: PluginManifest, *, core_version: str) -> None:
        self.calls.append((manifest, core_version))


class RejectingCompatibility:
    def validate(self, manifest: PluginManifest, *, core_version: str) -> None:
        raise ValueError(f"{manifest.name} is incompatible with {core_version}")


def _manifest(name: str = "example") -> PluginManifest:
    return PluginManifest(
        name=name,
        version="1.0.0",
        kind=PluginKind.PROVIDER,
        api_version=1,
        requires_core=">=0.1,<1",
    )


def test_registry_validates_before_registration() -> None:
    compatibility = RecordingCompatibility()
    registry = CapabilityPackRegistry(compatibility, core_version="0.1.0")
    manifest = _manifest()

    registry.register(manifest)

    assert compatibility.calls == [(manifest, "0.1.0")]
    assert tuple(registry) == (manifest,)


def test_rejected_manifest_does_not_mutate_registry() -> None:
    registry = CapabilityPackRegistry(RejectingCompatibility())

    with pytest.raises(ValueError, match="incompatible"):
        registry.register(_manifest())

    assert tuple(registry) == ()


def test_duplicate_manifest_name_is_rejected() -> None:
    registry = CapabilityPackRegistry(RecordingCompatibility())
    registry.register(_manifest())

    with pytest.raises(ValueError, match="duplicate"):
        registry.register(_manifest())

