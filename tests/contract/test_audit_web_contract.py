"""Administrator-only Audit search HTTP contracts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from pangi.adapters.inbound.web import create_web_app
from pangi.application.contracts.audit import AuditEventDraft, AuditStoreQuery
from pangi.application.contracts.auth import AuthenticatedPrincipal, SessionView
from pangi.application.contracts.readiness import ReadinessReport
from pangi.application.services.audit import AuditQueryService, core_audit_redaction_service
from pangi.domain.audit import AuditEvent, AuditOutcome
from pangi.domain.auth import UserRole, UserStatus

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class Runtime:
    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


class Readiness:
    def report(self) -> ReadinessReport:
        return ReadinessReport()


class AuthSessions:
    def __init__(self, role: UserRole) -> None:
        principal = AuthenticatedPrincipal(
            "admin-user-000001" if role is UserRole.ADMIN else "member-user-00001",
            "Actor",
            role,
            UserStatus.ACTIVE,
        )
        self.view = SessionView(
            principal,
            NOW + timedelta(hours=12),
            NOW + timedelta(minutes=30),
            False,
        )

    async def current_session(self, *, session_token: str) -> SessionView:
        assert session_token == "s" * 43
        return self.view


class AuditStore:
    def __init__(self, event: AuditEvent) -> None:
        self.event = event
        self.queries: list[AuditStoreQuery] = []

    async def list_events(self, query: AuditStoreQuery) -> tuple[AuditEvent, ...]:
        self.queries.append(query)
        return (self.event,)


class Unused:
    """Placeholder for routes outside this contract."""


def _static_root(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<h1>Pangi</h1>", "utf-8")
    return root


def _event() -> AuditEvent:
    return core_audit_redaction_service().prepare(
        AuditEventDraft(
            actor_id="admin-user-000001",
            action="bootstrap.admin_created",
            resource_type="user",
            resource_id="created-user-000001",
            outcome=AuditOutcome.SUCCEEDED,
            created_at=NOW,
            after_summary={"role": "admin", "status": "active"},
        ),
        event_id="audit-event-identifier-01",
    )


def _app(tmp_path: Path, *, role: UserRole, store: AuditStore):
    unused = Unused()
    return create_web_app(
        runtime_backend=Runtime(),
        readiness_probe=Readiness(),
        audit_operations=AuditQueryService(store),
        bootstrap_admin=unused,
        auth_sessions=AuthSessions(role),
        run_operations=unused,
        run_cancellations=unused,
        run_events=unused,
        run_queue_metrics=unused,
        static_root=_static_root(tmp_path),
    )


def _authenticate(client: TestClient) -> None:
    client.cookies.set("pangi_session", "s" * 43)


def test_admin_can_filter_audit_events_without_raw_management_values(
    tmp_path: Path,
) -> None:
    store = AuditStore(_event())
    with TestClient(
        _app(tmp_path, role=UserRole.ADMIN, store=store),
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
        _authenticate(client)
        response = client.get(
            "/api/v1/audit-events",
            params={
                "action": "bootstrap.admin_created",
                "resource_type": "user",
                "resource_id": "created-user-000001",
                "outcome": "succeeded",
                "limit": 25,
            },
        )
        invalid = client.get("/api/v1/audit-events?action=INVALID")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["action"] == "bootstrap.admin_created"
    assert payload["items"][0]["outcome"] == "succeeded"
    assert "password" not in response.text.casefold()
    query = store.queries[0]
    assert query.actions == ("bootstrap.admin_created",)
    assert query.resource_type == "user"
    assert query.outcomes == (AuditOutcome.SUCCEEDED,)
    assert query.limit == 26
    assert invalid.status_code == 400


def test_audit_endpoint_requires_authentication_and_active_admin(tmp_path: Path) -> None:
    store = AuditStore(_event())
    with TestClient(
        _app(tmp_path, role=UserRole.MEMBER, store=store),
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
        unauthenticated = client.get("/api/v1/audit-events")
        _authenticate(client)
        member = client.get("/api/v1/audit-events")

    assert unauthenticated.status_code == 401
    assert member.status_code == 403
    assert store.queries == []
