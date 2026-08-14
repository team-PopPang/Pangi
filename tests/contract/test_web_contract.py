"""Stable Web shell, authentication, health, and browser-security contracts."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pangi.adapters.inbound.web import create_web_app
from pangi.application.contracts.auth import (
    AuthenticatedPrincipal,
    IssuedSession,
    SessionView,
)
from pangi.application.contracts.bootstrap import BootstrapAdminResult
from pangi.application.contracts.readiness import (
    ReadinessCheckResult,
    ReadinessReport,
    ReadinessState,
)
from pangi.application.ports.auth import AuthenticationRequiredError, PermissionDeniedError
from pangi.domain.auth import UserRole, UserStatus


class RecordingRuntime:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.started = False
        self.calls: list[str] = []
        self._fail_start = fail_start

    async def start(self) -> None:
        self.calls.append("start")
        if self._fail_start:
            raise RuntimeError("startup failed")
        self.started = True

    async def close(self) -> None:
        self.calls.append("close")
        self.started = False


class RuntimeReadiness:
    def __init__(self, runtime: RecordingRuntime) -> None:
        self._runtime = runtime

    def report(self) -> ReadinessReport:
        state = ReadinessState.READY if self._runtime.started else ReadinessState.NOT_READY
        return ReadinessReport(
            checks=(ReadinessCheckResult("runtime", state, "safe runtime status"),)
        )


class RecordingBootstrapAdmin:
    async def issue_url(self, *, rotate: bool = False):
        raise AssertionError("Web adapter must not issue Bootstrap URLs")

    async def create_admin(
        self,
        *,
        token: str,
        local_id: str,
        display_name: str,
        password: str,
    ) -> BootstrapAdminResult:
        return BootstrapAdminResult("user-identifier-1", local_id, display_name)


class RecordingAuthSessions:
    def __init__(self) -> None:
        self.login_calls: list[tuple[str, str, str]] = []
        self.logout_calls: list[tuple[str, str]] = []
        principal = AuthenticatedPrincipal(
            "user-identifier-1",
            "Owner",
            UserRole.ADMIN,
            UserStatus.ACTIVE,
        )
        expires_at = datetime.now(UTC) + timedelta(hours=12)
        self.view = SessionView(
            principal,
            expires_at,
            expires_at - timedelta(hours=11, minutes=30),
            False,
        )

    async def login(
        self,
        *,
        local_id: str,
        password: str,
        source: str,
    ) -> IssuedSession:
        self.login_calls.append((local_id, password, source))
        return IssuedSession("s" * 43, "c" * 43, self.view)

    async def current_session(self, *, session_token: str) -> SessionView:
        if session_token not in {"s" * 43, "n" * 43}:
            raise AuthenticationRequiredError("missing")
        return self.view

    async def rotate(
        self,
        *,
        session_token: str,
        csrf_token: str,
    ) -> IssuedSession:
        if session_token != "s" * 43 or csrf_token != "c" * 43:
            raise AuthenticationRequiredError("missing")
        return IssuedSession("n" * 43, "r" * 43, self.view)

    async def logout(self, *, session_token: str, csrf_token: str) -> None:
        self.logout_calls.append((session_token, csrf_token))


class FailingAuthSessions(RecordingAuthSessions):
    async def login(
        self,
        *,
        local_id: str,
        password: str,
        source: str,
    ) -> IssuedSession:
        del local_id, password, source
        raise RuntimeError("internal-secret-value")


def _static_root(tmp_path: Path) -> Path:
    static_root = tmp_path / "static"
    assets = static_root / "assets"
    assets.mkdir(parents=True)
    (static_root / "index.html").write_text("<h1>Pangi Admin</h1>", "utf-8")
    (assets / "main-deadbeef.js").write_text("export {};", "utf-8")
    return static_root


def test_health_spa_assets_and_security_headers_are_stable(tmp_path: Path) -> None:
    runtime = RecordingRuntime()
    app = create_web_app(
        runtime_backend=runtime,
        readiness_probe=RuntimeReadiness(runtime),
        bootstrap_admin=RecordingBootstrapAdmin(),
        auth_sessions=RecordingAuthSessions(),
        static_root=_static_root(tmp_path),
    )

    without_lifespan = TestClient(app)
    not_ready = without_lifespan.get("/health/ready")
    without_lifespan.close()

    assert not_ready.status_code == 503
    assert not_ready.json()["status"] == "not_ready"

    with TestClient(app) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        root = client.get("/")
        nested_route = client.get("/settings/users")
        asset = client.get("/assets/main-deadbeef.js")
        missing_api = client.get("/api/v1/missing")
        missing_asset = client.get("/assets/missing.js")

        assert live.status_code == 200
        assert live.json()["product"] == "pangi"
        assert live.json()["status"] == "live"
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert root.text == nested_route.text == "<h1>Pangi Admin</h1>"
        assert root.headers["cache-control"] == "no-cache"
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert missing_api.status_code == 404
        assert missing_api.json()["error"]["code"] == "not_found"
        assert missing_api.json()["error"]["request_id"].startswith("req_")
        assert missing_asset.status_code == 404
        assert live.headers["x-content-type-options"] == "nosniff"
        assert live.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in live.headers["content-security-policy"]

    assert runtime.calls == ["start", "close"]
    assert runtime.started is False


def test_lifespan_closes_backend_when_startup_fails(tmp_path: Path) -> None:
    runtime = RecordingRuntime(fail_start=True)
    app = create_web_app(
        runtime_backend=runtime,
        readiness_probe=RuntimeReadiness(runtime),
        bootstrap_admin=RecordingBootstrapAdmin(),
        auth_sessions=RecordingAuthSessions(),
        static_root=_static_root(tmp_path),
    )

    with pytest.raises(RuntimeError, match="startup failed"), TestClient(app):
        pass

    assert runtime.calls == ["start", "close"]


def test_auth_cookie_transport_csrf_and_role_dependency_contracts(tmp_path: Path) -> None:
    runtime = RecordingRuntime()
    auth = RecordingAuthSessions()
    app = create_web_app(
        runtime_backend=runtime,
        readiness_probe=RuntimeReadiness(runtime),
        bootstrap_admin=RecordingBootstrapAdmin(),
        auth_sessions=auth,
        static_root=_static_root(tmp_path),
    )

    with TestClient(app, base_url="https://pangi.example") as secure_client:
        login = secure_client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://pangi.example"},
            json={"local_id": "owner", "password": "correct password"},
        )
        session = secure_client.get("/api/v1/auth/session")
        missing_origin = secure_client.post(
            "/api/v1/auth/session/rotate",
            headers={"X-CSRF-Token": "c" * 43},
        )
        rotated = secure_client.post(
            "/api/v1/auth/session/rotate",
            headers={"Origin": "https://pangi.example", "X-CSRF-Token": "c" * 43},
        )

    cookie_headers = login.headers.get_list("set-cookie")
    assert login.status_code == 200
    assert session.status_code == 200
    assert any(
        header.startswith("__Host-pangi_session=")
        and "HttpOnly" in header
        and "Secure" in header
        and "SameSite=lax" in header
        for header in cookie_headers
    )
    assert any(
        header.startswith("__Host-pangi_csrf=")
        and "HttpOnly" not in header
        and "Secure" in header
        for header in cookie_headers
    )
    assert missing_origin.status_code == 403
    assert missing_origin.json()["error"]["code"] == "csrf_rejected"
    assert rotated.status_code == 200
    assert auth.login_calls == [("owner", "correct password", "testclient")]

    admin = auth.view.principal
    member = AuthenticatedPrincipal(
        "user-identifier-2",
        "Member",
        UserRole.MEMBER,
        UserStatus.ACTIVE,
    )
    require_admin = app.state.require_roles(UserRole.ADMIN)
    assert asyncio.run(require_admin(admin)) == admin
    with pytest.raises(PermissionDeniedError):
        asyncio.run(require_admin(member))


def test_non_loopback_plain_http_login_is_rejected(tmp_path: Path) -> None:
    runtime = RecordingRuntime()
    auth = RecordingAuthSessions()
    app = create_web_app(
        runtime_backend=runtime,
        readiness_probe=RuntimeReadiness(runtime),
        bootstrap_admin=RecordingBootstrapAdmin(),
        auth_sessions=auth,
        static_root=_static_root(tmp_path),
    )

    with TestClient(app, base_url="http://pangi.example") as client:
        response = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://pangi.example"},
            json={"local_id": "owner", "password": "correct password"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "secure_transport_required"
    assert auth.login_calls == []


def test_unexpected_api_error_uses_safe_envelope(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = RecordingRuntime()
    app = create_web_app(
        runtime_backend=runtime,
        readiness_probe=RuntimeReadiness(runtime),
        bootstrap_admin=RecordingBootstrapAdmin(),
        auth_sessions=FailingAuthSessions(),
        static_root=_static_root(tmp_path),
    )

    with TestClient(
        app,
        base_url="https://pangi.example",
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://pangi.example"},
            json={"local_id": "owner", "password": "internal-secret-value"},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["request_id"].startswith("req_")
    assert "internal-secret-value" not in response.text
    assert "internal-secret-value" not in caplog.text
