"""Deterministic, runtime-free OpenAPI document generation."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from pangi.adapters.inbound.web import create_web_app
from pangi.application.contracts.auth import IssuedSession, SessionView
from pangi.application.contracts.bootstrap import BootstrapAdminResult, BootstrapIssueResult
from pangi.application.contracts.readiness import ReadinessReport


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


def generate_openapi_document() -> dict[str, object]:
    """Build the API schema without starting SQLite or reading runtime configuration."""

    static_root = Path(str(resources.files("pangi.web").joinpath("static")))
    app = create_web_app(
        runtime_backend=_SchemaOnlyRuntime(),
        readiness_probe=_SchemaOnlyReadiness(),
        bootstrap_admin=_SchemaOnlyBootstrapAdmin(),
        auth_sessions=_SchemaOnlyAuthSessions(),
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
