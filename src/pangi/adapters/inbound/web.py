"""FastAPI adapter for the local Admin shell and runtime health."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from pangi._version import __version__
from pangi.application.ports.readiness import ReadinessProbe
from pangi.application.ports.runtime import RuntimeBackend

_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    )
)
_RESERVED_ROUTE_ROOTS = frozenset({"api", "assets", "health"})


def create_web_app(
    *,
    runtime_backend: RuntimeBackend,
    readiness_probe: ReadinessProbe,
    static_root: Path,
) -> FastAPI:
    """Create an ASGI application without importing concrete outbound adapters."""

    index_file = static_root / "index.html"
    assets_root = static_root / "assets"
    if not index_file.is_file() or not assets_root.is_dir():
        raise RuntimeError("packaged Admin assets are incomplete")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            await runtime_backend.start()
            yield
        finally:
            await runtime_backend.close()

    app = FastAPI(
        title="Pangi Admin API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/assets/") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.get("/health/live", include_in_schema=False)
    async def live() -> JSONResponse:
        return JSONResponse(
            {
                "schema_version": 1,
                "product": "pangi",
                "status": "live",
                "version": __version__,
            }
        )

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        report = readiness_probe.report()
        return JSONResponse(report.as_dict(), status_code=200 if report.ready else 503)

    app.mount("/assets", StaticFiles(directory=assets_root), name="assets")

    @app.get("/{route_path:path}", include_in_schema=False)
    async def spa_fallback(route_path: str) -> FileResponse:
        route_root = route_path.partition("/")[0]
        if route_root in _RESERVED_ROUTE_ROOTS:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})

    return app
