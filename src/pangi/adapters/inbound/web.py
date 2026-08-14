"""FastAPI adapter for the local Admin shell and runtime health."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import RequestResponseEndpoint

from pangi._version import __version__
from pangi.application.ports.bootstrap_admin import (
    BootstrapAdminPort,
    BootstrapAlreadyConfiguredError,
    BootstrapIdentityConflictError,
    InvalidBootstrapGrantError,
)
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
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


class _BootstrapAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    token: str = Field(min_length=20, max_length=256)
    local_id: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=256)


def create_web_app(
    *,
    runtime_backend: RuntimeBackend,
    readiness_probe: ReadinessProbe,
    bootstrap_admin: BootstrapAdminPort,
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

    def error_response(
        request: Request,
        *,
        status_code: int,
        code: str,
        message: str,
    ) -> JSONResponse:
        request_id = str(getattr(request.state, "request_id", "req_unavailable"))
        return JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": request_id,
                }
            },
            status_code=status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return error_response(
            request,
            status_code=422,
            code="invalid_request",
            message="The request could not be validated",
        )

    @app.middleware("http")
    async def security_headers(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        supplied_request_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if _REQUEST_ID.fullmatch(supplied_request_id)
            else f"req_{uuid.uuid4().hex}"
        )
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
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

    @app.post("/api/v1/bootstrap/admin", include_in_schema=False)
    async def create_bootstrap_admin(
        payload: _BootstrapAdminRequest,
        request: Request,
    ) -> JSONResponse:
        origin = request.headers.get("origin")
        request_origin = f"{request.url.scheme}://{request.url.netloc}"
        if origin is not None and origin.rstrip("/") != request_origin:
            return error_response(
                request,
                status_code=403,
                code="origin_not_allowed",
                message="The request origin is not allowed",
            )
        try:
            result = await bootstrap_admin.create_admin(
                token=payload.token,
                local_id=payload.local_id,
                display_name=payload.display_name,
                password=payload.password,
            )
        except ValueError:
            return error_response(
                request,
                status_code=422,
                code="invalid_request",
                message="The request could not be validated",
            )
        except InvalidBootstrapGrantError:
            return error_response(
                request,
                status_code=400,
                code="bootstrap_unavailable",
                message="Bootstrap Grant is invalid or unavailable",
            )
        except BootstrapAlreadyConfiguredError:
            return error_response(
                request,
                status_code=409,
                code="bootstrap_closed",
                message="Bootstrap is already configured",
            )
        except BootstrapIdentityConflictError:
            return error_response(
                request,
                status_code=409,
                code="identity_unavailable",
                message="The requested local identity is unavailable",
            )
        return JSONResponse(
            {"admin": result.as_dict()},
            status_code=201,
        )

    app.mount("/assets", StaticFiles(directory=assets_root), name="assets")

    @app.get("/{route_path:path}", include_in_schema=False)
    async def spa_fallback(route_path: str) -> FileResponse:
        route_root = route_path.partition("/")[0]
        if route_root in _RESERVED_ROUTE_ROOTS:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})

    return app
