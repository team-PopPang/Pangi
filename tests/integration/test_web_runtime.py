"""Composed FastAPI and SQLite runtime integration tests."""

import asyncio
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.factory import build_bootstrap_admin_for_cli
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


def test_composed_bootstrap_api_creates_admin_with_safe_errors(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    issued = asyncio.run(build_bootstrap_admin_for_cli(paths, config).issue_url())
    assert issued.bootstrap_url is not None
    token = urlsplit(issued.bootstrap_url).fragment
    password = "correct horse battery staple"
    app = create_asgi_app(paths, config)

    with TestClient(app) as client:
        wrong_origin = client.post(
            "/api/v1/bootstrap/admin",
            headers={"Origin": "https://attacker.example"},
            json={
                "token": token,
                "local_id": "owner",
                "display_name": "Owner",
                "password": password,
            },
        )
        created = client.post(
            "/api/v1/bootstrap/admin",
            headers={"Origin": "http://testserver", "X-Request-ID": "request_12345678"},
            json={
                "token": token,
                "local_id": "owner",
                "display_name": "Owner",
                "password": password,
            },
        )
        reused = client.post(
            "/api/v1/bootstrap/admin",
            json={
                "token": token,
                "local_id": "another-owner",
                "display_name": "Another Owner",
                "password": password,
            },
        )
        invalid = client.post(
            "/api/v1/bootstrap/admin",
            json={
                "token": token,
                "local_id": "x",
                "display_name": "Owner",
                "password": password,
                "unexpected": token,
            },
        )

    assert wrong_origin.status_code == 403
    assert created.status_code == 201
    assert created.headers["x-request-id"] == "request_12345678"
    assert created.json()["admin"]["role"] == "admin"
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "bootstrap_unavailable"
    assert reused.json()["error"]["request_id"].startswith("req_")
    assert invalid.status_code == 422
    combined_errors = wrong_origin.text + reused.text + invalid.text
    assert token not in combined_errors
    assert password not in combined_errors
