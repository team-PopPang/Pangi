"""FastAPI adapter for the local Admin shell, authentication, and health."""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from pangi._version import __version__
from pangi.adapters.inbound.web_contracts import (
    BootstrapAdminRequest,
    BootstrapAdminResponse,
    ErrorEnvelope,
    LoginRequest,
    SessionEnvelope,
)
from pangi.application.contracts.auth import AuthenticatedPrincipal, IssuedSession
from pangi.application.ports.auth import (
    AuthenticationError,
    AuthenticationRequiredError,
    AuthSessionPort,
    CsrfRejectedError,
    InvalidCredentialsError,
    LoginRateLimitedError,
)
from pangi.application.ports.bootstrap_admin import (
    BootstrapAdminPort,
    BootstrapAlreadyConfiguredError,
    BootstrapIdentityConflictError,
    InvalidBootstrapGrantError,
)
from pangi.application.ports.readiness import ReadinessProbe
from pangi.application.ports.runtime import RuntimeBackend
from pangi.application.services.auth import ensure_role
from pangi.domain.auth import UserRole

_LOGGER = logging.getLogger(__name__)
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
_RESERVED_ROUTE_ROOTS = frozenset(
    {"api", "assets", "docs", "health", "openapi.json", "redoc"}
)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_SESSION_COOKIE = "pangi_session"
_CSRF_COOKIE = "pangi_csrf"
_SECURE_SESSION_COOKIE = "__Host-pangi_session"
_SECURE_CSRF_COOKIE = "__Host-pangi_csrf"


@dataclass(frozen=True, slots=True)
class _CookiePolicy:
    session_name: str
    csrf_name: str
    secure: bool


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _cookie_policy(request: Request) -> _CookiePolicy:
    if request.url.scheme.casefold() == "https":
        return _CookiePolicy(_SECURE_SESSION_COOKIE, _SECURE_CSRF_COOKIE, True)
    return _CookiePolicy(_SESSION_COOKIE, _CSRF_COOKIE, False)


def _allows_session_transport(request: Request) -> bool:
    if request.url.scheme.casefold() == "https":
        return True
    peer = request.client.host if request.client is not None else ""
    return _is_loopback_host(request.url.hostname or "") and _is_loopback_host(peer)


def _request_origin(request: Request) -> str:
    return f"{request.url.scheme.casefold()}://{request.url.netloc.casefold()}"


def _origin_allowed(request: Request, *, required: bool) -> bool:
    expected = _request_origin(request)
    origin = request.headers.get("origin")
    if origin is not None:
        return origin.rstrip("/").casefold() == expected
    referer = request.headers.get("referer")
    if referer is not None:
        prefix = referer.partition("//")
        if not prefix[1]:
            return False
        referer_origin = f"{prefix[0]}//{prefix[2].partition('/')[0]}"
        return referer_origin.casefold() == expected
    return not required


def _set_session_cookies(response: Response, request: Request, issued: IssuedSession) -> None:
    policy = _cookie_policy(request)
    remaining = max(
        1,
        int((issued.view.expires_at - datetime.now(UTC)).total_seconds()),
    )
    response.set_cookie(
        key=policy.session_name,
        value=issued.session_token,
        max_age=remaining,
        expires=issued.view.expires_at,
        path="/",
        secure=policy.secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        key=policy.csrf_name,
        value=issued.csrf_token,
        max_age=remaining,
        expires=issued.view.expires_at,
        path="/",
        secure=policy.secure,
        httponly=False,
        samesite="lax",
    )


def _clear_session_cookies(response: Response, request: Request) -> None:
    policy = _cookie_policy(request)
    response.delete_cookie(
        policy.session_name,
        path="/",
        secure=policy.secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        policy.csrf_name,
        path="/",
        secure=policy.secure,
        httponly=False,
        samesite="lax",
    )


def _session_token(request: Request) -> str:
    if not _allows_session_transport(request):
        raise AuthenticationRequiredError("An active Session is required")
    token = request.cookies.get(_cookie_policy(request).session_name)
    if token is None:
        raise AuthenticationRequiredError("An active Session is required")
    return token


def _csrf_token(request: Request) -> str:
    policy = _cookie_policy(request)
    cookie = request.cookies.get(policy.csrf_name)
    header = request.headers.get("x-csrf-token")
    if (
        cookie is None
        or header is None
        or not 20 <= len(header) <= 256
        or not secrets.compare_digest(cookie, header)
    ):
        raise CsrfRejectedError("The CSRF token is invalid")
    return header


def create_web_app(
    *,
    runtime_backend: RuntimeBackend,
    readiness_probe: ReadinessProbe,
    bootstrap_admin: BootstrapAdminPort,
    auth_sessions: AuthSessionPort,
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
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        request_id = str(getattr(request.state, "request_id", "req_unavailable"))
        envelope = ErrorEnvelope.create(
            code=code,
            message=message,
            request_id=request_id,
        )
        return JSONResponse(
            envelope.model_dump(mode="json"),
            status_code=status_code,
            headers=headers,
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

    @app.exception_handler(AuthenticationError)
    async def authentication_error(
        request: Request,
        error: AuthenticationError,
    ) -> JSONResponse:
        if isinstance(error, LoginRateLimitedError):
            return error_response(
                request,
                status_code=429,
                code="login_rate_limited",
                message="Too many login attempts; try again later",
                headers={"Retry-After": str(error.retry_after_seconds)},
            )
        if isinstance(error, InvalidCredentialsError):
            return error_response(
                request,
                status_code=401,
                code="invalid_credentials",
                message="The local identifier or password is invalid",
            )
        if isinstance(error, AuthenticationRequiredError):
            return error_response(
                request,
                status_code=401,
                code="authentication_required",
                message="Authentication is required",
            )
        if isinstance(error, CsrfRejectedError):
            return error_response(
                request,
                status_code=403,
                code="csrf_rejected",
                message="The request could not be verified",
            )
        return error_response(
            request,
            status_code=403,
            code="permission_denied",
            message="The authenticated role is not allowed",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        if error.status_code == 404:
            return error_response(
                request,
                status_code=404,
                code="not_found",
                message="The requested resource was not found",
            )
        if error.status_code == 405:
            return error_response(
                request,
                status_code=405,
                code="method_not_allowed",
                message="The request method is not allowed",
            )
        return error_response(
            request,
            status_code=error.status_code,
            code="request_rejected",
            message="The request was rejected",
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        request_id = str(getattr(request.state, "request_id", "req_unavailable"))
        _LOGGER.error(
            "Unhandled API error request_id=%s error_type=%s",
            request_id,
            type(error).__name__,
        )
        return error_response(
            request,
            status_code=500,
            code="internal_error",
            message="An internal error occurred",
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

    async def current_principal(request: Request) -> AuthenticatedPrincipal:
        view = await auth_sessions.current_session(session_token=_session_token(request))
        return view.principal

    def require_roles(
        *allowed_roles: UserRole,
    ) -> Callable[..., Awaitable[AuthenticatedPrincipal]]:
        async def dependency(
            principal: Annotated[AuthenticatedPrincipal, Depends(current_principal)],
        ) -> AuthenticatedPrincipal:
            return ensure_role(principal, allowed_roles)

        return dependency

    app.state.require_roles = require_roles

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

    @app.post(
        "/api/v1/bootstrap/admin",
        operation_id="createBootstrapAdmin",
        response_model=BootstrapAdminResponse,
        status_code=201,
        responses={
            400: {"model": ErrorEnvelope, "description": "Bootstrap grant unavailable"},
            403: {"model": ErrorEnvelope, "description": "Origin rejected"},
            409: {"model": ErrorEnvelope, "description": "Bootstrap state conflict"},
            422: {"model": ErrorEnvelope, "description": "Request validation failed"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
        },
    )
    async def create_bootstrap_admin(
        payload: BootstrapAdminRequest,
        request: Request,
    ) -> BootstrapAdminResponse | JSONResponse:
        if not _origin_allowed(request, required=False):
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
        return BootstrapAdminResponse.from_contract(result)

    @app.post(
        "/api/v1/auth/login",
        operation_id="login",
        response_model=SessionEnvelope,
        responses={
            400: {"model": ErrorEnvelope, "description": "Secure transport required"},
            401: {"model": ErrorEnvelope, "description": "Credentials rejected"},
            403: {"model": ErrorEnvelope, "description": "Origin rejected"},
            422: {"model": ErrorEnvelope, "description": "Request validation failed"},
            429: {
                "model": ErrorEnvelope,
                "description": "Login rate limit exceeded",
                "headers": {
                    "Retry-After": {
                        "description": "Seconds before another login attempt",
                        "schema": {"type": "integer", "minimum": 1},
                    }
                },
            },
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
        },
    )
    async def login(
        payload: LoginRequest,
        request: Request,
        response: Response,
    ) -> SessionEnvelope | JSONResponse:
        if not _allows_session_transport(request):
            return error_response(
                request,
                status_code=400,
                code="secure_transport_required",
                message="HTTPS or a loopback address is required for login",
            )
        if not _origin_allowed(request, required=False):
            return error_response(
                request,
                status_code=403,
                code="origin_not_allowed",
                message="The request origin is not allowed",
            )
        source = request.client.host if request.client is not None else "unknown"
        issued = await auth_sessions.login(
            local_id=payload.local_id,
            password=payload.password,
            source=source,
        )
        _set_session_cookies(response, request, issued)
        return SessionEnvelope.from_contract(issued.view)

    @app.get(
        "/api/v1/auth/session",
        operation_id="getAuthSession",
        response_model=SessionEnvelope,
        responses={
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
        },
    )
    async def session(request: Request) -> SessionEnvelope:
        view = await auth_sessions.current_session(session_token=_session_token(request))
        return SessionEnvelope.from_contract(view)

    @app.post(
        "/api/v1/auth/session/rotate",
        operation_id="rotateAuthSession",
        response_model=SessionEnvelope,
        responses={
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            403: {"model": ErrorEnvelope, "description": "CSRF or origin rejected"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
        },
    )
    async def rotate_session(request: Request, response: Response) -> SessionEnvelope:
        if not _origin_allowed(request, required=True):
            raise CsrfRejectedError("The request origin is invalid")
        issued = await auth_sessions.rotate(
            session_token=_session_token(request),
            csrf_token=_csrf_token(request),
        )
        _set_session_cookies(response, request, issued)
        return SessionEnvelope.from_contract(issued.view)

    @app.post(
        "/api/v1/auth/logout",
        operation_id="logout",
        response_class=Response,
        status_code=204,
        responses={
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            403: {"model": ErrorEnvelope, "description": "CSRF or origin rejected"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
        },
    )
    async def logout(request: Request) -> Response:
        if not _origin_allowed(request, required=True):
            raise CsrfRejectedError("The request origin is invalid")
        await auth_sessions.logout(
            session_token=_session_token(request),
            csrf_token=_csrf_token(request),
        )
        response = Response(status_code=204)
        _clear_session_cookies(response, request)
        return response

    app.mount("/assets", StaticFiles(directory=assets_root), name="assets")

    @app.get("/{route_path:path}", include_in_schema=False)
    async def spa_fallback(route_path: str) -> FileResponse:
        route_root = route_path.partition("/")[0]
        if route_root in _RESERVED_ROUTE_ROOTS:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})

    return app
