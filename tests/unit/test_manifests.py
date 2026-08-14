"""Plugin manifest validation tests."""

import pytest

from pangi.plugins import PluginKind, PluginManifest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", " example"),
        ("version", ""),
        ("kind", "provider"),
        ("api_version", 0),
        ("requires_core", ""),
    ],
)
def test_manifest_rejects_invalid_required_values(field: str, value: object) -> None:
    values: dict[str, object] = {
        "name": "example",
        "version": "1.0.0",
        "kind": PluginKind.PROVIDER,
        "api_version": 1,
        "requires_core": ">=0.1,<1",
    }
    values[field] = value

    with pytest.raises(ValueError):
        PluginManifest(**values)  # type: ignore[arg-type]
