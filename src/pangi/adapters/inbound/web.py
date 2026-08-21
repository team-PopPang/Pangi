"""FastAPI adapter for the local Admin shell, authentication, and health."""

from __future__ import annotations

import asyncio
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

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from pangi._version import __version__
from pangi.adapters.inbound.web_contracts import (
    AuditEventListEnvelope,
    BootstrapAdminRequest,
    BootstrapAdminResponse,
    ErrorEnvelope,
    LoginRequest,
    ModelPolicyActivateRequest,
    ModelPolicyActivationEnvelope,
    ModelPolicyEvaluateRequest,
    ModelPolicyEvaluationEnvelope,
    ModelPolicyListEnvelope,
    RunCancellationEnvelope,
    RunCreateRequest,
    RunEnvelope,
    RunEventListEnvelope,
    RunEventResponse,
    RunListEnvelope,
    RunQueueMetricsResponse,
    RunSubmissionEnvelope,
    SessionEnvelope,
)
from pangi.application.contracts.audit import AuditListQuery
from pangi.application.contracts.auth import AuthenticatedPrincipal, IssuedSession
from pangi.application.contracts.guardrails import GuardrailBlockedError
from pangi.application.contracts.model_policy_management import ModelPolicyListQuery
from pangi.application.contracts.run_events import RunEventStreamPolicy
from pangi.application.contracts.runs import RunListQuery
from pangi.application.ports.audit import (
    AuditOperationError,
    AuditOperations,
    AuditPersistenceError,
    InvalidAuditCursorError,
)
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
from pangi.application.ports.model_policy_management import (
    InvalidModelPolicyCursorError,
    ModelPolicyEvalUnavailableError,
    ModelPolicyManagementError,
    ModelPolicyManagementOperations,
    ModelPolicyNotFoundError,
    ModelPolicyPersistenceError,
)
from pangi.application.ports.readiness import ReadinessProbe
from pangi.application.ports.run_events import (
    InvalidRunEventCursorError,
    RunCancellationOperations,
    RunEventError,
    RunEventNotFoundError,
    RunEventOperations,
    RunEventPersistenceError,
    RunQueueMetricOperations,
    RunQueueMetricPersistenceError,
)
from pangi.application.ports.run_queue import (
    RunQueueConflictError,
    RunQueueError,
    RunQueueNotFoundError,
    RunQueuePersistenceError,
    RunQueueUnavailableError,
)
from pangi.application.ports.run_submissions import RunSubmissionOperations
from pangi.application.ports.runs import (
    IdempotencyUnavailableError,
    InvalidRunCursorError,
    RunNotFoundError,
    RunOperationError,
    RunOperations,
    RunPersistenceError,
)
from pangi.application.ports.runtime import RuntimeBackend
from pangi.application.services.auth import ensure_role
from pangi.domain.audit import AuditOutcome
from pangi.domain.auth import UserRole
from pangi.domain.guardrails import GuardrailErrorCode
from pangi.domain.runs import PrincipalChannel, RunState

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
_RESERVED_ROUTE_ROOTS = frozenset({"api", "assets", "docs", "health", "openapi.json", "redoc"})
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_SESSION_COOKIE = "pangi_session"
_CSRF_COOKIE = "pangi_csrf"
_SECURE_SESSION_COOKIE = "__Host-pangi_session"
_SECURE_CSRF_COOKIE = "__Host-pangi_csrf"
_EVENT_INDEX = re.compile(r"^(?:0|[1-9][0-9]{0,18})$")
_DEFAULT_EVENT_STREAM_POLICY = RunEventStreamPolicy()

Sleeper = Callable[[float], Awaitable[None]]


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


def _event_index(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    if _EVENT_INDEX.fullmatch(value) is None:
        raise InvalidRunEventCursorError("The Run Event cursor is invalid")
    index = int(value)
    if index > 9_223_372_036_854_775_807:
        raise InvalidRunEventCursorError("The Run Event cursor is invalid")
    return index


def create_web_app(
    *,
    runtime_backend: RuntimeBackend,
    readiness_probe: ReadinessProbe,
    audit_operations: AuditOperations,
    bootstrap_admin: BootstrapAdminPort,
    auth_sessions: AuthSessionPort,
    run_operations: RunOperations,
    run_cancellations: RunCancellationOperations,
    run_events: RunEventOperations,
    run_queue_metrics: RunQueueMetricOperations,
    static_root: Path,
    run_submissions: RunSubmissionOperations | None = None,
    model_policy_operations: ModelPolicyManagementOperations | None = None,
    event_stream_policy: RunEventStreamPolicy = _DEFAULT_EVENT_STREAM_POLICY,
    event_stream_sleeper: Sleeper = asyncio.sleep,
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

    def run_submission_service() -> RunSubmissionOperations:
        if run_submissions is None:
            raise HTTPException(status_code=503, detail="Run submission is unavailable")
        return run_submissions

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

    @app.exception_handler(GuardrailBlockedError)
    async def guardrail_blocked_error(
        request: Request,
        error: GuardrailBlockedError,
    ) -> JSONResponse:
        if error.code is GuardrailErrorCode.RATE_LIMIT_EXCEEDED:
            retry_after = error.decision.retry_after_seconds
            return error_response(
                request,
                status_code=429,
                code=error.code.value,
                message="Too many Run requests; try again later",
                headers={"Retry-After": str(retry_after or 1)},
            )
        if error.code is GuardrailErrorCode.EXPLICIT_SKILL_UNAVAILABLE:
            return error_response(
                request,
                status_code=503,
                code=error.code.value,
                message="Explicit Skill execution is unavailable",
            )
        if error.code in {
            GuardrailErrorCode.PRINCIPAL_INACTIVE,
            GuardrailErrorCode.PRINCIPAL_ID_MISMATCH,
            GuardrailErrorCode.PRINCIPAL_ROLE_MISMATCH,
            GuardrailErrorCode.EXPLICIT_SKILL_DENIED,
        }:
            return error_response(
                request,
                status_code=403,
                code=error.code.value,
                message="The Run request is not allowed",
            )
        return error_response(
            request,
            status_code=422,
            code=error.code.value,
            message="The Run request did not pass input validation",
        )

    @app.exception_handler(AuditOperationError)
    async def audit_operation_error(
        request: Request,
        error: AuditOperationError,
    ) -> JSONResponse:
        if isinstance(error, InvalidAuditCursorError):
            return error_response(
                request,
                status_code=400,
                code=error.code,
                message="The Audit cursor or filter is invalid",
            )
        if isinstance(error, AuditPersistenceError):
            return error_response(
                request,
                status_code=503,
                code=error.code,
                message="Audit Events are unavailable",
            )
        return error_response(
            request,
            status_code=409,
            code=error.code,
            message="The Audit operation could not be completed",
        )

    @app.exception_handler(RunOperationError)
    async def run_operation_error(
        request: Request,
        error: RunOperationError,
    ) -> JSONResponse:
        if isinstance(error, InvalidRunCursorError):
            return error_response(
                request,
                status_code=400,
                code=error.code,
                message="The Run cursor is invalid",
            )
        if isinstance(error, RunNotFoundError):
            return error_response(
                request,
                status_code=404,
                code=error.code,
                message="The Run was not found",
            )
        if isinstance(error, (RunPersistenceError, IdempotencyUnavailableError)):
            return error_response(
                request,
                status_code=503,
                code=error.code,
                message="The Run store is unavailable",
            )
        return error_response(
            request,
            status_code=409,
            code=error.code,
            message="The Run operation conflicts with its current state",
        )

    @app.exception_handler(RunEventError)
    async def run_event_error(
        request: Request,
        error: RunEventError,
    ) -> JSONResponse:
        if isinstance(error, InvalidRunEventCursorError):
            return error_response(
                request,
                status_code=400,
                code=error.code,
                message="The Run Event cursor is invalid",
            )
        if isinstance(error, RunEventNotFoundError):
            return error_response(
                request,
                status_code=404,
                code=error.code,
                message="The Run was not found",
            )
        if isinstance(
            error,
            (RunEventPersistenceError, RunQueueMetricPersistenceError),
        ):
            return error_response(
                request,
                status_code=503,
                code=error.code,
                message="Run operational data is unavailable",
            )
        return error_response(
            request,
            status_code=409,
            code=error.code,
            message="The Run Event operation conflicts with its current state",
        )

    @app.exception_handler(RunQueueError)
    async def run_queue_error(
        request: Request,
        error: RunQueueError,
    ) -> JSONResponse:
        if isinstance(error, RunQueueNotFoundError):
            return error_response(
                request,
                status_code=404,
                code="run_not_found",
                message="The Run was not found",
            )
        if isinstance(error, RunQueueConflictError):
            return error_response(
                request,
                status_code=409,
                code=error.code,
                message="The Run cannot be cancelled in its current state",
            )
        if isinstance(error, (RunQueuePersistenceError, RunQueueUnavailableError)):
            return error_response(
                request,
                status_code=503,
                code=error.code,
                message="The Run Queue is unavailable",
            )
        return error_response(
            request,
            status_code=409,
            code=error.code,
            message="The Run Queue operation conflicts with its current state",
        )

    @app.exception_handler(ModelPolicyManagementError)
    async def model_policy_management_error(
        request: Request,
        error: ModelPolicyManagementError,
    ) -> JSONResponse:
        if isinstance(error, InvalidModelPolicyCursorError):
            return error_response(
                request,
                status_code=400,
                code=error.code,
                message="The Model Policy cursor is invalid",
            )
        if isinstance(error, ModelPolicyNotFoundError):
            return error_response(
                request,
                status_code=404,
                code=error.code,
                message="The Model Policy version was not found",
            )
        if isinstance(error, ModelPolicyEvalUnavailableError):
            return error_response(
                request,
                status_code=503,
                code=error.code,
                message="Model Policy Eval is unavailable",
            )
        if isinstance(error, ModelPolicyPersistenceError):
            return error_response(
                request,
                status_code=503,
                code=error.code,
                message="Model Policy management data is unavailable",
            )
        return error_response(
            request,
            status_code=409,
            code=error.code,
            message="The Model Policy operation conflicts with its current state",
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

    @app.get(
        "/api/v1/audit-events",
        operation_id="listAuditEvents",
        response_model=AuditEventListEnvelope,
        responses={
            400: {"model": ErrorEnvelope, "description": "Invalid Audit cursor or filter"},
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            403: {"model": ErrorEnvelope, "description": "Administrator required"},
            422: {"model": ErrorEnvelope, "description": "Request validation failed"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
            503: {"model": ErrorEnvelope, "description": "Audit store unavailable"},
        },
    )
    async def list_audit_events(
        principal: AuthenticatedPrincipal = Depends(current_principal),  # noqa: B008
        actor_id: str | None = None,
        actions: Annotated[list[str] | None, Query(alias="action")] = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        outcomes: Annotated[
            list[AuditOutcome] | None,
            Query(alias="outcome"),
        ] = None,
        created_from: Annotated[datetime | None, Query(alias="from")] = None,
        created_to: Annotated[datetime | None, Query(alias="to")] = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> AuditEventListEnvelope:
        try:
            query = AuditListQuery(
                actor_id=actor_id,
                actions=tuple(actions or ()),
                resource_type=resource_type,
                resource_id=resource_id,
                outcomes=tuple(outcomes or ()),
                created_from=created_from,
                created_to=created_to,
                limit=limit,
                cursor=cursor,
            )
        except ValueError as error:
            raise InvalidAuditCursorError("The Audit filter is invalid") from error
        page = await audit_operations.list_events(actor=principal, query=query)
        return AuditEventListEnvelope.from_contract(page)

    def model_policy_service() -> ModelPolicyManagementOperations:
        if model_policy_operations is None:
            raise ModelPolicyPersistenceError("Model Policy management is not composed")
        return model_policy_operations

    @app.get(
        "/api/v1/model-policies",
        operation_id="listModelPolicies",
        response_model=ModelPolicyListEnvelope,
        responses={
            400: {"model": ErrorEnvelope, "description": "Invalid Policy cursor"},
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            403: {"model": ErrorEnvelope, "description": "Administrator required"},
            422: {"model": ErrorEnvelope, "description": "Request validation failed"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
            503: {"model": ErrorEnvelope, "description": "Policy store unavailable"},
        },
    )
    async def list_model_policies(
        principal: AuthenticatedPrincipal = Depends(current_principal),  # noqa: B008
        limit: int = 50,
        cursor: str | None = None,
    ) -> ModelPolicyListEnvelope:
        try:
            query = ModelPolicyListQuery(limit=limit, cursor=cursor)
        except ValueError as error:
            raise InvalidModelPolicyCursorError(
                "The Model Policy cursor or limit is invalid"
            ) from error
        page = await model_policy_service().list_policies(
            actor=principal,
            query=query,
        )
        return ModelPolicyListEnvelope.from_contract(page)

    @app.post(
        "/api/v1/model-policies/{policy_id}/versions/{version}/evaluate",
        operation_id="evaluateModelPolicy",
        response_model=ModelPolicyEvaluationEnvelope,
        status_code=202,
        responses={
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            403: {"model": ErrorEnvelope, "description": "CSRF or role rejected"},
            404: {"model": ErrorEnvelope, "description": "Policy version not found"},
            409: {"model": ErrorEnvelope, "description": "Policy state conflict"},
            422: {"model": ErrorEnvelope, "description": "Request validation failed"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
            503: {"model": ErrorEnvelope, "description": "Eval runtime unavailable"},
        },
    )
    async def evaluate_model_policy(
        policy_id: str,
        version: str,
        payload: ModelPolicyEvaluateRequest,
        request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=255),
        ],
        principal: AuthenticatedPrincipal = Depends(current_principal),  # noqa: B008
    ) -> ModelPolicyEvaluationEnvelope | JSONResponse:
        if not _origin_allowed(request, required=True):
            raise CsrfRejectedError("The request origin is invalid")
        _csrf_token(request)
        try:
            evaluation = await model_policy_service().evaluate_policy(
                actor=principal,
                policy_id=policy_id,
                version=version,
                candidate_fingerprint=payload.candidate_fingerprint,
                idempotency_key=idempotency_key,
            )
        except ValueError:
            return error_response(
                request,
                status_code=422,
                code="invalid_request",
                message="The request could not be validated",
            )
        return ModelPolicyEvaluationEnvelope.from_contract(evaluation)

    @app.post(
        "/api/v1/model-policies/{policy_id}/versions/{version}/activate",
        operation_id="activateModelPolicy",
        response_model=ModelPolicyActivationEnvelope,
        responses={
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            403: {"model": ErrorEnvelope, "description": "CSRF or role rejected"},
            404: {"model": ErrorEnvelope, "description": "Policy version not found"},
            409: {"model": ErrorEnvelope, "description": "Eval or Policy conflict"},
            422: {"model": ErrorEnvelope, "description": "Request validation failed"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
            503: {"model": ErrorEnvelope, "description": "Policy or Eval unavailable"},
        },
    )
    async def activate_model_policy(
        policy_id: str,
        version: str,
        payload: ModelPolicyActivateRequest,
        request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=255),
        ],
        principal: AuthenticatedPrincipal = Depends(current_principal),  # noqa: B008
    ) -> ModelPolicyActivationEnvelope | JSONResponse:
        if not _origin_allowed(request, required=True):
            raise CsrfRejectedError("The request origin is invalid")
        _csrf_token(request)
        try:
            activation = await model_policy_service().activate_policy(
                actor=principal,
                policy_id=policy_id,
                version=version,
                candidate_fingerprint=payload.candidate_fingerprint,
                impact_fingerprint=payload.impact_fingerprint,
                eval_run_id=payload.eval_run_id,
                idempotency_key=idempotency_key,
            )
        except ValueError:
            return error_response(
                request,
                status_code=422,
                code="invalid_request",
                message="The request could not be validated",
            )
        return ModelPolicyActivationEnvelope.from_contract(activation)

    @app.post(
        "/api/v1/runs",
        operation_id="createRun",
        status_code=202,
        response_model=RunSubmissionEnvelope,
        responses={
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            403: {"model": ErrorEnvelope, "description": "CSRF, origin, or policy rejected"},
            409: {"model": ErrorEnvelope, "description": "Idempotency conflict"},
            422: {"model": ErrorEnvelope, "description": "Request or Guardrail validation failed"},
            429: {"model": ErrorEnvelope, "description": "Run rate limit exceeded"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
            503: {"model": ErrorEnvelope, "description": "Run submission unavailable"},
        },
    )
    async def create_run(
        payload: RunCreateRequest,
        request: Request,
        response: Response,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=255),
        ],
        principal: AuthenticatedPrincipal = Depends(current_principal),  # noqa: B008
    ) -> RunSubmissionEnvelope | JSONResponse:
        if not _origin_allowed(request, required=True):
            raise CsrfRejectedError("The request origin is invalid")
        _csrf_token(request)
        try:
            result = await run_submission_service().submit_run(
                actor=principal,
                text=payload.text,
                idempotency_key=idempotency_key,
                thread_key=payload.thread_key,
                explicit_skill=payload.explicit_skill,
            )
        except ValueError:
            return error_response(
                request,
                status_code=422,
                code="invalid_request",
                message="The request could not be validated",
            )
        response.headers["Location"] = f"/api/v1/runs/{result.run.id}"
        return RunSubmissionEnvelope.from_contract(result)

    @app.get(
        "/api/v1/runs",
        operation_id="listRuns",
        response_model=RunListEnvelope,
        responses={
            400: {"model": ErrorEnvelope, "description": "Invalid Run cursor"},
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            422: {"model": ErrorEnvelope, "description": "Request validation failed"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
            503: {"model": ErrorEnvelope, "description": "Run store unavailable"},
        },
    )
    async def list_runs(
        principal: AuthenticatedPrincipal = Depends(current_principal),  # noqa: B008
        states: Annotated[list[RunState] | None, Query(alias="state")] = None,
        triggers: Annotated[
            list[PrincipalChannel] | None,
            Query(alias="trigger"),
        ] = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> RunListEnvelope:
        try:
            query = RunListQuery(
                states=tuple(states or ()),
                triggers=tuple(triggers or ()),
                limit=limit,
                cursor=cursor,
            )
        except ValueError as error:
            raise InvalidRunCursorError("The Run cursor is invalid") from error
        page = await run_operations.list_runs(actor=principal, query=query)
        return RunListEnvelope.from_contract(page)

    @app.get(
        "/api/v1/runs/metrics",
        operation_id="getRunQueueMetrics",
        response_model=RunQueueMetricsResponse,
        responses={
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            403: {"model": ErrorEnvelope, "description": "Administrator required"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
            503: {"model": ErrorEnvelope, "description": "Metric store unavailable"},
        },
    )
    async def get_run_queue_metrics(
        principal: AuthenticatedPrincipal = Depends(current_principal),  # noqa: B008
    ) -> RunQueueMetricsResponse:
        metrics = await run_queue_metrics.queue_metrics(actor=principal)
        return RunQueueMetricsResponse.from_contract(metrics)

    @app.get(
        "/api/v1/runs/{run_id}",
        operation_id="getRun",
        response_model=RunEnvelope,
        responses={
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            404: {"model": ErrorEnvelope, "description": "Run not found"},
            422: {"model": ErrorEnvelope, "description": "Request validation failed"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
            503: {"model": ErrorEnvelope, "description": "Run store unavailable"},
        },
    )
    async def get_run(
        run_id: str,
        principal: AuthenticatedPrincipal = Depends(current_principal),  # noqa: B008
    ) -> RunEnvelope:
        run = await run_operations.get_run(actor=principal, run_id=run_id)
        return RunEnvelope.from_domain(run)

    @app.post(
        "/api/v1/runs/{run_id}/cancel",
        operation_id="cancelRun",
        response_model=RunCancellationEnvelope,
        responses={
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            403: {"model": ErrorEnvelope, "description": "CSRF or origin rejected"},
            404: {"model": ErrorEnvelope, "description": "Run not found"},
            409: {"model": ErrorEnvelope, "description": "Run state conflict"},
            422: {"model": ErrorEnvelope, "description": "Request validation failed"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
            503: {"model": ErrorEnvelope, "description": "Run Queue unavailable"},
        },
    )
    async def cancel_run(
        run_id: str,
        request: Request,
        principal: AuthenticatedPrincipal = Depends(current_principal),  # noqa: B008
    ) -> RunCancellationEnvelope:
        if not _origin_allowed(request, required=True):
            raise CsrfRejectedError("The request origin is invalid")
        _csrf_token(request)
        result = await run_cancellations.cancel_run(actor=principal, run_id=run_id)
        return RunCancellationEnvelope.from_contract(result)

    @app.get(
        "/api/v1/runs/{run_id}/events",
        operation_id="getRunEvents",
        response_model=RunEventListEnvelope,
        responses={
            200: {
                "description": "JSON Event page or resumable Event stream",
                "content": {
                    "text/event-stream": {
                        "schema": {"type": "string"},
                    }
                },
            },
            400: {"model": ErrorEnvelope, "description": "Invalid Event cursor"},
            401: {"model": ErrorEnvelope, "description": "Authentication required"},
            404: {"model": ErrorEnvelope, "description": "Run not found"},
            422: {"model": ErrorEnvelope, "description": "Request validation failed"},
            500: {"model": ErrorEnvelope, "description": "Unexpected server error"},
            503: {"model": ErrorEnvelope, "description": "Event store unavailable"},
        },
    )
    async def get_run_events(
        run_id: str,
        request: Request,
        principal: AuthenticatedPrincipal = Depends(current_principal),  # noqa: B008
        after: str | None = None,
        limit: int = 100,
    ) -> RunEventListEnvelope | StreamingResponse:
        initial_after = _event_index(after, default=0)
        if not 1 <= limit <= 100:
            raise InvalidRunEventCursorError("The Run Event page limit is invalid")
        accepts_sse = "text/event-stream" in request.headers.get("accept", "").casefold()
        if not accepts_sse:
            page = await run_events.list_events(
                actor=principal,
                run_id=run_id,
                after_index=initial_after,
                limit=limit,
            )
            return RunEventListEnvelope.from_contract(page)

        last_event_id = request.headers.get("last-event-id")
        stream_after = _event_index(last_event_id, default=initial_after)
        session_token = _session_token(request)
        await run_events.list_events(
            actor=principal,
            run_id=run_id,
            after_index=stream_after,
            limit=1,
        )

        async def event_stream() -> AsyncIterator[str]:
            cursor = stream_after
            loop = asyncio.get_running_loop()
            next_keepalive = loop.time() + event_stream_policy.keepalive_interval_seconds
            while True:
                try:
                    session_view = await auth_sessions.current_session(session_token=session_token)
                    page = await run_events.list_events(
                        actor=session_view.principal,
                        run_id=run_id,
                        after_index=cursor,
                        limit=event_stream_policy.batch_size,
                    )
                except (AuthenticationError, RunEventNotFoundError):
                    return
                for event in page.items:
                    payload = RunEventResponse.from_domain(event).model_dump_json()
                    yield f"id: {event.index}\nevent: run-event\ndata: {payload}\n\n"
                    cursor = event.index
                if page.terminal and page.next_after_index is None:
                    return
                if page.next_after_index is not None:
                    continue
                await event_stream_sleeper(event_stream_policy.poll_interval_seconds)
                if loop.time() >= next_keepalive:
                    yield ": keepalive\n\n"
                    next_keepalive = loop.time() + event_stream_policy.keepalive_interval_seconds

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
            },
        )

    app.mount("/assets", StaticFiles(directory=assets_root), name="assets")

    @app.get("/{route_path:path}", include_in_schema=False)
    async def spa_fallback(route_path: str) -> FileResponse:
        route_root = route_path.partition("/")[0]
        if route_root in _RESERVED_ROUTE_ROOTS:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})

    return app
