"""Write-time Run Event redaction across SQLite, JSON delivery, and SSE."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.factory import (
    build_bootstrap_admin_for_cli,
    build_run_service,
    build_sqlite_database,
)
from pangi.adapters.outbound.persistence.sqlite.run_events import SqliteRunEventStore
from pangi.adapters.outbound.persistence.sqlite.runs import (
    SqliteRunQueueStore,
    SqliteRunStore,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.contracts.run_events import RunEventDraft
from pangi.application.contracts.runs import RunCreateRecord
from pangi.application.contracts.telemetry import TelemetryRedactionError
from pangi.bootstrap import create_asgi_app
from pangi.domain.auth import UserRole
from pangi.domain.runs import (
    EventVisibility,
    Principal,
    PrincipalChannel,
    Run,
    RunEvent,
    RunRequest,
    RunState,
)
from pangi.domain.telemetry import TelemetryRedactionErrorCode

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _runtime(tmp_path: Path) -> tuple[RuntimePaths, PangiConfig]:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    return paths, config


async def _insert_user(database: SqliteDatabase, user_id: str) -> None:
    async with database.create() as unit_of_work:
        await unit_of_work.connection.execute(
            "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
            "VALUES (?, 'Member', 'member', 'active', ?, ?)",
            (user_id, NOW.isoformat(), NOW.isoformat()),
        )
        await unit_of_work.commit()


def _run_record(secret: str) -> RunCreateRecord:
    run_id = "run-telemetry-identifier-0001"
    request = RunRequest(
        request_id="request-telemetry-identifier-0001",
        principal=Principal(
            "member-user-00001",
            UserRole.MEMBER,
            PrincipalChannel.DASHBOARD,
        ),
        text="safe request",
        idempotency_key="telemetry-request-once",
        created_at=NOW,
    )
    run = Run(run_id, request, RunState.RECEIVED, NOW)
    return RunCreateRecord(
        run=run,
        first_event=RunEvent(
            run_id=run_id,
            index=1,
            type="run.received",
            visibility=EventVisibility.PUBLIC,
            created_at=NOW,
            message=f"token={secret}",
            attributes={"api_key": secret},
        ),
        route_key="runs.create",
        request_fingerprint="f" * 64,
        recorded_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )


def test_every_sqlite_event_writer_redacts_and_rejection_keeps_indexes_atomic(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        paths, config = _runtime(tmp_path)
        database = SqliteDatabase(paths, config.storage)
        secret = "sqlite-private-value"
        await database.start()
        try:
            await _insert_user(database, "member-user-00001")
            record = _run_record(secret)
            await SqliteRunStore(database).create_or_replay(record)

            event_store = SqliteRunEventStore(database)
            generic = await event_store.append_event(
                RunEventDraft(
                    run_id=record.run.id,
                    type="run.progress",
                    visibility=EventVisibility.PUBLIC,
                    created_at=NOW + timedelta(seconds=1),
                    message=f"password={secret}\r\nCafe\u0301",
                    attributes={"authorization": secret},
                )
            )
            assert generic.message == "password=[REDACTED]\nCafé"
            assert generic.attributes == {"authorization": "[REDACTED]"}

            with pytest.raises(TelemetryRedactionError) as captured:
                await event_store.append_event(
                    RunEventDraft(
                        run_id=record.run.id,
                        type="run.invalid",
                        visibility=EventVisibility.PUBLIC,
                        created_at=NOW + timedelta(seconds=2),
                        attributes={"raw_prompt": secret},
                    )
                )
            assert (
                captured.value.code
                is TelemetryRedactionErrorCode.FORBIDDEN_EVENT_FIELD
            )

            next_event = await event_store.append_event(
                RunEventDraft(
                    run_id=record.run.id,
                    type="run.progress",
                    visibility=EventVisibility.PUBLIC,
                    created_at=NOW + timedelta(seconds=3),
                    message="safe next event",
                )
            )
            assert next_event.index == 3

            queue = SqliteRunQueueStore(database)
            queued = await queue.enqueue(
                run_id=record.run.id,
                expected_revision=record.run.revision,
                at=NOW + timedelta(seconds=4),
            )
            claim = await queue.claim_next(
                worker_id="worker-telemetry-identifier-0001",
                at=NOW + timedelta(seconds=5),
                lease_expires_at=NOW + timedelta(seconds=35),
            )
            assert queued.state is RunState.QUEUED
            assert claim is not None
            recovered = await queue.abandon_claim(
                run_id=record.run.id,
                worker_id=claim.worker_id,
                at=NOW + timedelta(seconds=6),
                reason=f"token={secret}",
            )
            assert recovered.requeued_run_ids == (record.run.id,)

            async with database.create() as unit_of_work:
                rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT event_index, message, attributes_json FROM run_events "
                    "WHERE run_id = ? ORDER BY event_index",
                    (record.run.id,),
                )
                await unit_of_work.commit()
            serialized = json.dumps(
                [dict(row) for row in rows],
                ensure_ascii=False,
            )
            assert [int(row["event_index"]) for row in rows] == list(
                range(1, len(rows) + 1)
            )
            assert secret not in serialized
            assert "[REDACTED]" in serialized
        finally:
            await database.close()

    asyncio.run(scenario())


def test_composed_json_and_sse_deliver_only_persisted_safe_events(
    tmp_path: Path,
) -> None:
    paths, config = _runtime(tmp_path)
    issued = asyncio.run(build_bootstrap_admin_for_cli(paths, config).issue_url())
    assert issued.bootstrap_url is not None
    token = urlsplit(issued.bootstrap_url).fragment
    password = "correct horse battery staple"

    with TestClient(
        create_asgi_app(paths, config),
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
        created = client.post(
            "/api/v1/bootstrap/admin",
            json={
                "token": token,
                "local_id": "owner",
                "display_name": "Owner",
                "password": password,
            },
        )
    assert created.status_code == 201
    admin_id = str(created.json()["admin"]["user_id"])
    secret = "delivery-private-value"

    async def prepare_events() -> str:
        database = build_sqlite_database(paths, config)
        await database.start()
        try:
            run = await build_run_service(database).create_run(
                RunRequest(
                    request_id="request-delivery-identifier-0001",
                    principal=Principal(
                        admin_id,
                        UserRole.ADMIN,
                        PrincipalChannel.DASHBOARD,
                    ),
                    text="safe request",
                    idempotency_key="delivery-request-once",
                    created_at=NOW,
                ),
                route_key="runs.create",
            )
            await SqliteRunEventStore(database).append_event(
                RunEventDraft(
                    run_id=run.run.id,
                    type="run.progress",
                    visibility=EventVisibility.PUBLIC,
                    created_at=NOW + timedelta(seconds=1),
                    message=f"password={secret}",
                    attributes={"api_key": secret},
                )
            )
            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "UPDATE runs SET state = 'completed', finished_at = ?, updated_at = ? "
                    "WHERE id = ?",
                    (
                        (NOW + timedelta(seconds=2)).isoformat(),
                        (NOW + timedelta(seconds=2)).isoformat(),
                        run.run.id,
                    ),
                )
                await unit_of_work.commit()
            return run.run.id
        finally:
            await database.close()

    run_id = asyncio.run(prepare_events())

    with TestClient(
        create_asgi_app(paths, config),
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"local_id": "owner", "password": password},
        )
        assert login.status_code == 200
        listed = client.get(f"/api/v1/runs/{run_id}/events")
        streamed = client.get(
            f"/api/v1/runs/{run_id}/events",
            headers={"Accept": "text/event-stream"},
        )

    assert listed.status_code == streamed.status_code == 200
    assert secret not in listed.text
    assert secret not in streamed.text
    assert "[REDACTED]" in listed.text
    assert "[REDACTED]" in streamed.text
