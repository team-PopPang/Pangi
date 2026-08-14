"""Composed FastAPI and SQLite runtime integration tests."""

import asyncio
import sqlite3
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

    with TestClient(
        app,
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
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

    with TestClient(
        app,
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
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
            headers={
                "Origin": "http://127.0.0.1:8787",
                "X-Request-ID": "request_12345678",
            },
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
        wrong_login = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://127.0.0.1:8787"},
            json={"local_id": "owner", "password": "wrong password"},
        )
        login = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://127.0.0.1:8787"},
            json={"local_id": "owner", "password": password},
        )
        session_token = client.cookies.get("pangi_session")
        csrf_token = client.cookies.get("pangi_csrf")
        session = client.get("/api/v1/auth/session")
        missing_csrf = client.post(
            "/api/v1/auth/session/rotate",
            headers={"Origin": "http://127.0.0.1:8787"},
        )
        rotated = client.post(
            "/api/v1/auth/session/rotate",
            headers={
                "Origin": "http://127.0.0.1:8787",
                "X-CSRF-Token": str(csrf_token),
            },
        )
        rotated_session_token = client.cookies.get("pangi_session")
        rotated_csrf_token = client.cookies.get("pangi_csrf")
        missing_origin = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": str(rotated_csrf_token)},
        )
        logout = client.post(
            "/api/v1/auth/logout",
            headers={
                "Origin": "http://127.0.0.1:8787",
                "X-CSRF-Token": str(rotated_csrf_token),
            },
        )
        after_logout = client.get("/api/v1/auth/session")

    assert wrong_origin.status_code == 403
    assert created.status_code == 201
    assert created.headers["x-request-id"] == "request_12345678"
    assert created.json()["admin"]["role"] == "admin"
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "bootstrap_unavailable"
    assert reused.json()["error"]["request_id"].startswith("req_")
    assert invalid.status_code == 422
    assert wrong_login.status_code == 401
    assert wrong_login.json()["error"]["code"] == "invalid_credentials"
    assert login.status_code == 200
    assert login.json()["session"]["principal"]["role"] == "admin"
    assert session_token is not None
    assert csrf_token is not None
    login_cookies = login.headers.get_list("set-cookie")
    assert any(
        header.startswith("pangi_session=")
        and "HttpOnly" in header
        and "Secure" not in header
        and "Domain=" not in header
        for header in login_cookies
    )
    assert session.status_code == 200
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_rejected"
    assert rotated.status_code == 200
    assert rotated_session_token not in {None, session_token}
    assert rotated_csrf_token not in {None, csrf_token}
    assert missing_origin.status_code == 403
    assert logout.status_code == 204
    assert after_logout.status_code == 401
    assert after_logout.json()["error"]["code"] == "authentication_required"
    combined_errors = wrong_origin.text + reused.text + invalid.text
    assert token not in combined_errors
    assert password not in combined_errors

    with sqlite3.connect(paths.database_file) as connection:
        stored = connection.execute(
            "SELECT token_hash, csrf_hash, state FROM auth_sessions"
        ).fetchone()
    assert stored is not None
    assert stored[2] == "revoked"
    assert session_token not in stored
    assert csrf_token not in stored
    assert rotated_session_token not in stored
    assert rotated_csrf_token not in stored
