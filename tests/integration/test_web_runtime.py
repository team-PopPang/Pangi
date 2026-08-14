"""Composed FastAPI and SQLite runtime integration tests."""

from importlib import resources
from pathlib import Path

from fastapi.testclient import TestClient

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.locking import process_lock_available
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.bootstrap import create_asgi_app


def test_composed_app_starts_sqlite_and_serves_packaged_shell(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    app = create_asgi_app(paths, config)

    with TestClient(app) as client:
        ready = client.get("/health/ready")
        shell = client.get("/")

        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert {check["id"] for check in ready.json()["checks"]} == {
            "sqlite.runtime",
            "web.assets",
        }
        assert shell.status_code == 200
        assert "Pangi Admin" in shell.text
        assert paths.database_file.is_file()
        assert process_lock_available(paths.process_lock_file) is False

    assert process_lock_available(paths.process_lock_file) is True


def test_built_asset_manifest_and_index_are_packaged_resources() -> None:
    static_root = resources.files("pangi.web").joinpath("static")

    assert static_root.joinpath("index.html").is_file()
    assert static_root.joinpath("asset-manifest.json").is_file()
    assert any(item.name.endswith(".js") for item in static_root.joinpath("assets").iterdir())
