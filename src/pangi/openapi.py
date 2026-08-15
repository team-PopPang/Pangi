"""Deterministic, runtime-free OpenAPI document generation."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from pangi.adapters.inbound.web import create_web_app
from pangi.application.contracts.audit import AuditListPage, AuditListQuery
from pangi.application.contracts.auth import (
    AuthenticatedPrincipal,
    IssuedSession,
    SessionView,
)
from pangi.application.contracts.bootstrap import BootstrapAdminResult, BootstrapIssueResult
from pangi.application.contracts.readiness import ReadinessReport
from pangi.application.contracts.run_events import RunEventPage, RunQueueMetrics
from pangi.application.contracts.run_queue import RunCancellation
from pangi.application.contracts.runs import RunCreation, RunListPage, RunListQuery
from pangi.domain.runs import Run, RunRequest


def _schema_only_dependency() -> RuntimeError:
    return RuntimeError("schema-only dependencies cannot serve requests")


class _SchemaOnlyRuntime:
    async def start(self) -> None:
        raise _schema_only_dependency()

    async def close(self) -> None:
        raise _schema_only_dependency()


class _SchemaOnlyReadiness:
    def report(self) -> ReadinessReport:
        raise _schema_only_dependency()


class _SchemaOnlyBootstrapAdmin:
    async def issue_url(self, *, rotate: bool = False) -> BootstrapIssueResult:
        del rotate
        raise _schema_only_dependency()

    async def create_admin(
        self,
        *,
        token: str,
        local_id: str,
        display_name: str,
        password: str,
    ) -> BootstrapAdminResult:
        del token, local_id, display_name, password
        raise _schema_only_dependency()


class _SchemaOnlyAuditOperations:
    async def list_events(
        self,
        *,
        actor: AuthenticatedPrincipal,
        query: AuditListQuery,
    ) -> AuditListPage:
        del actor, query
        raise _schema_only_dependency()


class _SchemaOnlyAuthSessions:
    async def login(
        self,
        *,
        local_id: str,
        password: str,
        source: str,
    ) -> IssuedSession:
        del local_id, password, source
        raise _schema_only_dependency()

    async def current_session(self, *, session_token: str) -> SessionView:
        del session_token
        raise _schema_only_dependency()

    async def rotate(
        self,
        *,
        session_token: str,
        csrf_token: str,
    ) -> IssuedSession:
        del session_token, csrf_token
        raise _schema_only_dependency()

    async def logout(self, *, session_token: str, csrf_token: str) -> None:
        del session_token, csrf_token
        raise _schema_only_dependency()


class _SchemaOnlyRunOperations:
    async def create_run(self, request: RunRequest, *, route_key: str) -> RunCreation:
        del request, route_key
        raise _schema_only_dependency()

    async def get_run(self, *, actor: AuthenticatedPrincipal, run_id: str) -> Run:
        del actor, run_id
        raise _schema_only_dependency()

    async def list_runs(
        self,
        *,
        actor: AuthenticatedPrincipal,
        query: RunListQuery,
    ) -> RunListPage:
        del actor, query
        raise _schema_only_dependency()


class _SchemaOnlyRunCancellation:
    async def cancel_run(
        self,
        *,
        actor: AuthenticatedPrincipal,
        run_id: str,
    ) -> RunCancellation:
        del actor, run_id
        raise _schema_only_dependency()


class _SchemaOnlyRunEvents:
    async def list_events(
        self,
        *,
        actor: AuthenticatedPrincipal,
        run_id: str,
        after_index: int,
        limit: int,
    ) -> RunEventPage:
        del actor, run_id, after_index, limit
        raise _schema_only_dependency()


class _SchemaOnlyRunQueueMetrics:
    async def queue_metrics(
        self,
        *,
        actor: AuthenticatedPrincipal,
    ) -> RunQueueMetrics:
        del actor
        raise _schema_only_dependency()


def generate_openapi_document() -> dict[str, object]:
    """Build the API schema without starting SQLite or reading runtime configuration."""

    static_root = Path(str(resources.files("pangi.web").joinpath("static")))
    app = create_web_app(
        runtime_backend=_SchemaOnlyRuntime(),
        readiness_probe=_SchemaOnlyReadiness(),
        audit_operations=_SchemaOnlyAuditOperations(),
        bootstrap_admin=_SchemaOnlyBootstrapAdmin(),
        auth_sessions=_SchemaOnlyAuthSessions(),
        run_operations=_SchemaOnlyRunOperations(),
        run_cancellations=_SchemaOnlyRunCancellation(),
        run_events=_SchemaOnlyRunEvents(),
        run_queue_metrics=_SchemaOnlyRunQueueMetrics(),
        static_root=static_root,
    )
    return app.openapi()


def render_openapi_document() -> str:
    """Render the build-time API contract in a stable, reviewable form."""

    return json.dumps(
        generate_openapi_document(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
