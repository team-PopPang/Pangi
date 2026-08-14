"""CLI and import smoke tests."""

import subprocess
import sys
from importlib import resources

from pangi import __version__


def test_module_cli_reports_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "pangi", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == f"pangi {__version__}"


def test_import_does_not_load_optional_frameworks() -> None:
    code = """
import sys
import pangi

for module in ('fastapi', 'mcp', 'slack_sdk', 'typer'):
    assert module not in sys.modules, module
assert 'pangi.plugins' not in sys.modules
assert pangi.__all__ == ('PangiConfig', 'PangiRuntime', '__version__')
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_packaged_static_asset_boundary_exists() -> None:
    manifest = resources.files("pangi.web").joinpath("static/asset-manifest.json")

    assert manifest.is_file()
