"""Stable Web shell, health, and browser-security contracts."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pangi.adapters.inbound.web import create_web_app
from pangi.application.contracts.readiness import (
    ReadinessCheckResult,
    ReadinessReport,
    ReadinessState,
)


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
        state = (
            ReadinessState.READY if self._runtime.started else ReadinessState.NOT_READY
        )
        return ReadinessReport(
            checks=(ReadinessCheckResult("runtime", state, "safe runtime status"),)
        )


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
        static_root=_static_root(tmp_path),
    )

    with pytest.raises(RuntimeError, match="startup failed"), TestClient(app):
        pass

    assert runtime.calls == ["start", "close"]
