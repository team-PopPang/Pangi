"""Run HTTP, SSE resume, mutation security, and response-shape contracts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from pangi.adapters.inbound.web import create_web_app
from pangi.application.contracts.auth import AuthenticatedPrincipal, SessionView
from pangi.application.contracts.readiness import ReadinessReport
from pangi.application.contracts.run_events import (
    RunEventPage,
    RunEventStreamPolicy,
    RunQueueMetrics,
)
from pangi.application.contracts.run_queue import RunCancellation
from pangi.application.contracts.runs import RunListPage, RunSummary
from pangi.application.ports.run_events import RunEventNotFoundError
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.runs import (
    EventVisibility,
    Principal,
    PrincipalChannel,
    Run,
    RunEvent,
    RunRequest,
    RunState,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)
RUN_ID = "run-identifier-0001"


class Runtime:
    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


class Readiness:
    def report(self) -> ReadinessReport:
        return ReadinessReport()


class Bootstrap:
    async def create_admin(self, **_kwargs: object) -> None:
        raise AssertionError("bootstrap is outside this contract")


class AuthSessions:
    def __init__(self, role: UserRole = UserRole.ADMIN) -> None:
        self.calls = 0
        self.principal = AuthenticatedPrincipal(
            "admin-user-000001" if role is UserRole.ADMIN else "member-user-00001",
            "Actor",
            role,
            UserStatus.ACTIVE,
        )
        self.view = SessionView(
            self.principal,
            NOW + timedelta(hours=12),
            NOW + timedelta(minutes=30),
            False,
        )

    async def current_session(self, *, session_token: str) -> SessionView:
        assert session_token == "s" * 43
        self.calls += 1
        return self.view


def _run(state: RunState = RunState.QUEUED) -> Run:
    terminal = state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
    return Run(
        id=RUN_ID,
        request=RunRequest(
            request_id="request-identifier-1",
            principal=Principal(
                "member-user-00001",
                UserRole.MEMBER,
                PrincipalChannel.DASHBOARD,
            ),
            text="normalized request text",
            idempotency_key="must-not-cross-http",
            created_at=NOW,
        ),
        state=state,
        revision=2,
        updated_at=NOW + timedelta(seconds=2),
        finished_at=NOW + timedelta(seconds=2) if terminal else None,
        worker_id="worker-identifier-0001" if state is RunState.RUNNING else None,
        lease_expires_at=(
            NOW + timedelta(seconds=30) if state is RunState.RUNNING else None
        ),
        heartbeat_at=NOW if state is RunState.RUNNING else None,
    )


def _summary(run: Run) -> RunSummary:
    return RunSummary(
        id=run.id,
        request_id=run.request.request_id,
        principal_id=run.request.principal.user_id,
        trigger=run.request.principal.channel,
        state=run.state,
        mode=run.mode,
        skill_version_id=run.skill_version_id,
        revision=run.revision,
        created_at=run.request.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        warning_count=len(run.warnings),
        error_code=run.error_code,
    )


def _event(index: int) -> RunEvent:
    return RunEvent(
        run_id=RUN_ID,
        index=index,
        type="run.progress",
        visibility=EventVisibility.PUBLIC,
        created_at=NOW + timedelta(seconds=index),
        message="safe progress",
        attributes={"progress": index},
    )


class RunApi:
    def __init__(self) -> None:
        self.event_calls: list[tuple[str, int, int]] = []
        self.cancel_calls: list[str] = []

    async def list_runs(self, **_kwargs: object) -> RunListPage:
        run = _run()
        return RunListPage((_summary(run),), None)

    async def get_run(self, **_kwargs: object) -> Run:
        return _run()

    async def cancel_run(self, *, run_id: str, **_kwargs: object) -> RunCancellation:
        self.cancel_calls.append(run_id)
        return RunCancellation(_run(RunState.CANCELLED), True)

    async def list_events(
        self,
        *,
        run_id: str,
        after_index: int,
        limit: int,
        **_kwargs: object,
    ) -> RunEventPage:
        self.event_calls.append((run_id, after_index, limit))
        if run_id == "foreign-run-00001":
            raise RunEventNotFoundError("missing")
        return RunEventPage((_event(after_index + 1),), None, after_index >= 3)

    async def queue_metrics(self, **_kwargs: object) -> RunQueueMetrics:
        return RunQueueMetrics(2, 1, 1, NOW, 30.0)


class WaitingRunApi(RunApi):
    def __init__(self) -> None:
        super().__init__()
        self.in_read = False
        self.terminal = False

    async def list_events(
        self,
        *,
        run_id: str,
        after_index: int,
        limit: int,
        **_kwargs: object,
    ) -> RunEventPage:
        self.in_read = True
        try:
            self.event_calls.append((run_id, after_index, limit))
            return RunEventPage((), None, self.terminal)
        finally:
            self.in_read = False


def _static_root(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<h1>Pangi</h1>", "utf-8")
    return root


def _app(
    tmp_path: Path,
    *,
    auth: AuthSessions,
    api: RunApi,
    sleeper=None,
):
    options = {}
    if sleeper is not None:
        options["event_stream_sleeper"] = sleeper
    return create_web_app(
        runtime_backend=Runtime(),
        readiness_probe=Readiness(),
        bootstrap_admin=Bootstrap(),
        auth_sessions=auth,
        run_operations=api,
        run_cancellations=api,
        run_events=api,
        run_queue_metrics=api,
        static_root=_static_root(tmp_path),
        event_stream_policy=RunEventStreamPolicy(2, 0.001, 0.001),
        **options,
    )


def _authenticate(client: TestClient) -> None:
    client.cookies.set("pangi_session", "s" * 43)
    client.cookies.set("pangi_csrf", "c" * 43)


def test_run_json_routes_hide_worker_and_idempotency_data(tmp_path: Path) -> None:
    auth = AuthSessions()
    api = RunApi()
    with TestClient(
        _app(tmp_path, auth=auth, api=api),
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
        _authenticate(client)
        listed = client.get("/api/v1/runs")
        detail = client.get(f"/api/v1/runs/{RUN_ID}")
        events = client.get(f"/api/v1/runs/{RUN_ID}/events?after=1&limit=20")
        metrics = client.get("/api/v1/runs/metrics")

    assert listed.status_code == detail.status_code == events.status_code == 200
    assert metrics.status_code == 200
    assert listed.json()["items"][0]["id"] == RUN_ID
    detail_payload = detail.json()["run"]
    assert detail_payload["request"]["text"] == "normalized request text"
    assert "idempotency_key" not in detail_payload["request"]
    assert "worker_id" not in detail_payload
    assert "lease_expires_at" not in detail_payload
    assert events.json()["items"][0]["index"] == 2
    assert metrics.json() == {
        "queue_depth": 2,
        "running_count": 1,
        "expired_lease_count": 1,
        "oldest_queued_at": NOW.isoformat().replace("+00:00", "Z"),
        "oldest_queued_age_seconds": 30.0,
    }


def test_cancel_requires_matching_origin_and_csrf(tmp_path: Path) -> None:
    auth = AuthSessions()
    api = RunApi()
    with TestClient(
        _app(tmp_path, auth=auth, api=api),
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
        _authenticate(client)
        missing_origin = client.post(
            f"/api/v1/runs/{RUN_ID}/cancel",
            headers={"X-CSRF-Token": "c" * 43},
        )
        wrong_csrf = client.post(
            f"/api/v1/runs/{RUN_ID}/cancel",
            headers={
                "Origin": "http://127.0.0.1:8787",
                "X-CSRF-Token": "x" * 43,
            },
        )
        cancelled = client.post(
            f"/api/v1/runs/{RUN_ID}/cancel",
            headers={
                "Origin": "http://127.0.0.1:8787",
                "X-CSRF-Token": "c" * 43,
            },
        )

    assert missing_origin.status_code == wrong_csrf.status_code == 403
    assert cancelled.status_code == 200
    assert cancelled.json()["changed"] is True
    assert api.cancel_calls == [RUN_ID]


def test_sse_prefers_last_event_id_and_closes_after_terminal_page(tmp_path: Path) -> None:
    auth = AuthSessions()
    api = RunApi()
    with TestClient(
        _app(tmp_path, auth=auth, api=api),
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
        _authenticate(client)
        response = client.get(
            f"/api/v1/runs/{RUN_ID}/events?after=1",
            headers={"Accept": "text/event-stream", "Last-Event-ID": "3"},
        )
        invalid = client.get(
            f"/api/v1/runs/{RUN_ID}/events",
            headers={"Accept": "text/event-stream", "Last-Event-ID": "bad"},
        )
        foreign = client.get(
            "/api/v1/runs/foreign-run-00001/events",
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 4\nevent: run-event\ndata:" in response.text
    assert all(after_index == 3 for _run_id, after_index, _limit in api.event_calls[:2])
    assert api.event_calls[:2] == [(RUN_ID, 3, 1), (RUN_ID, 3, 2)]
    assert auth.calls >= 4
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_run_event_cursor"
    assert foreign.status_code == 404


def test_sse_releases_each_read_before_waiting(tmp_path: Path) -> None:
    auth = AuthSessions()
    api = WaitingRunApi()
    waits = 0

    async def sleeper(_seconds: float) -> None:
        nonlocal waits
        assert api.in_read is False
        waits += 1
        api.terminal = True

    with TestClient(
        _app(tmp_path, auth=auth, api=api, sleeper=sleeper),
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
        _authenticate(client)
        response = client.get(
            f"/api/v1/runs/{RUN_ID}/events",
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 200
    assert waits == 1
    assert len(api.event_calls) == 3
    assert auth.calls == 3
