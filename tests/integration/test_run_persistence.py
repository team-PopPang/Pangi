"""Run creation, idempotency, cursor, and owner-scope SQLite integration tests."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all, fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.runs import SqliteRunStore
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.runs import RunListQuery
from pangi.application.ports.runs import (
    IdempotencyConflictError,
    InvalidRunCursorError,
    RunNotFoundError,
    RunPersistenceError,
    RunPrincipalUnavailableError,
    RunRequestConflictError,
)
from pangi.application.services.runs import RunService
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.runs import AttachmentRef, Principal, PrincipalChannel, RunRequest, RunState

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _database(tmp_path: Path) -> SqliteDatabase:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    return SqliteDatabase(paths, config.storage)


async def _insert_user(
    database: SqliteDatabase,
    user_id: str,
    *,
    role: UserRole = UserRole.MEMBER,
    status: UserStatus = UserStatus.ACTIVE,
) -> None:
    async with database.create() as unit_of_work:
        timestamp = NOW.isoformat()
        await unit_of_work.connection.execute(
            "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, user_id, role.value, status.value, timestamp, timestamp),
        )
        await unit_of_work.commit()


def _request(
    user_id: str,
    *,
    request_id: str,
    idempotency_key: str,
    text: str = "이번 주 열린 이슈를 요약해줘",
    created_at: datetime = NOW,
    role: UserRole = UserRole.MEMBER,
) -> RunRequest:
    return RunRequest(
        request_id=request_id,
        principal=Principal(user_id, role, PrincipalChannel.DASHBOARD),
        text=text,
        idempotency_key=idempotency_key,
        created_at=created_at,
        attachments=(
            AttachmentRef(
                "attachment-ref-0001",
                display_name="issues.csv",
                media_type="text/csv",
                size_bytes=120,
                fingerprint="a" * 64,
            ),
        ),
    )


def _actor(user_id: str, *, role: UserRole = UserRole.MEMBER) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(user_id, user_id, role, UserStatus.ACTIVE)


def test_run_creation_replay_conflict_and_safe_first_event(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database, "member-user-00001")
            identifiers = iter(
                (
                    "run-identifier-0001",
                    "run-identifier-0002",
                    "run-identifier-0003",
                    "run-identifier-0004",
                )
            )
            service = RunService(
                SqliteRunStore(database),
                clock=lambda: NOW + timedelta(minutes=1),
                id_factory=lambda: next(identifiers),
            )
            first = await service.create_run(
                _request(
                    "member-user-00001",
                    request_id="request-identifier-1",
                    idempotency_key="request-once-1",
                ),
                route_key="runs.create",
            )
            replay = await service.create_run(
                _request(
                    "member-user-00001",
                    request_id="request-identifier-2",
                    idempotency_key="request-once-1",
                    created_at=NOW + timedelta(seconds=5),
                ),
                route_key="runs.create",
            )
            assert not first.replayed
            assert replay.replayed
            assert replay.run.id == first.run.id
            assert replay.run.request.request_id == "request-identifier-1"

            with pytest.raises(IdempotencyConflictError):
                await service.create_run(
                    _request(
                        "member-user-00001",
                        request_id="request-identifier-3",
                        idempotency_key="request-once-1",
                        text="같은 키의 다른 요청",
                    ),
                    route_key="runs.create",
                )
            with pytest.raises(RunRequestConflictError):
                await service.create_run(
                    _request(
                        "member-user-00001",
                        request_id="request-identifier-1",
                        idempotency_key="request-once-2",
                    ),
                    route_key="runs.create",
                )

            async with database.create() as unit_of_work:
                run_rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT id, request_text, attachments_json FROM runs",
                )
                event_rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT event_index, type, visibility, message, attributes_json "
                    "FROM run_events",
                )
                idempotency_rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT state, response_json, run_id FROM api_idempotency_records",
                )
                await unit_of_work.commit()
            assert len(run_rows) == len(event_rows) == len(idempotency_rows) == 1
            assert str(run_rows[0]["id"]) == first.run.id
            assert "attachment_body" not in str(run_rows[0]["attachments_json"])
            assert dict(event_rows[0]) == {
                "event_index": 1,
                "type": "run.received",
                "visibility": "public",
                "message": "Request received",
                "attributes_json": '{"trigger":"dashboard"}',
            }
            assert str(idempotency_rows[0]["state"]) == "completed"
            assert json.loads(str(idempotency_rows[0]["response_json"])) == {
                "run_id": first.run.id
            }
            assert str(idempotency_rows[0]["run_id"]) == first.run.id
        finally:
            await database.close()

    asyncio.run(scenario())


def test_concurrent_duplicate_creation_commits_one_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database, "member-user-00001")
            identifiers = iter(("run-identifier-0001", "run-identifier-0002"))
            service = RunService(
                SqliteRunStore(database),
                clock=lambda: NOW,
                id_factory=lambda: next(identifiers),
            )
            first, second = await asyncio.gather(
                service.create_run(
                    _request(
                        "member-user-00001",
                        request_id="request-identifier-1",
                        idempotency_key="concurrent-once",
                    ),
                    route_key="runs.create",
                ),
                service.create_run(
                    _request(
                        "member-user-00001",
                        request_id="request-identifier-2",
                        idempotency_key="concurrent-once",
                    ),
                    route_key="runs.create",
                ),
            )
            assert first.run.id == second.run.id
            assert {first.replayed, second.replayed} == {False, True}
            async with database.create() as unit_of_work:
                counts: list[int] = []
                for statement in (
                    "SELECT COUNT(*) FROM runs",
                    "SELECT COUNT(*) FROM run_events",
                    "SELECT COUNT(*) FROM api_idempotency_records",
                ):
                    row = await fetch_one(unit_of_work.connection, statement)
                    assert row is not None
                    counts.append(int(row[0]))
                await unit_of_work.commit()
            assert counts == [1, 1, 1]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_event_failure_rolls_back_run_and_idempotency_record(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database, "member-user-00001")
            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "CREATE TRIGGER reject_run_event BEFORE INSERT ON run_events "
                    "BEGIN SELECT RAISE(ABORT, 'fixture event failure'); END"
                )
                await unit_of_work.commit()
            service = RunService(
                SqliteRunStore(database),
                clock=lambda: NOW,
                id_factory=lambda: "run-identifier-0001",
            )
            with pytest.raises(RunPersistenceError):
                await service.create_run(
                    _request(
                        "member-user-00001",
                        request_id="request-identifier-1",
                        idempotency_key="rollback-once",
                    ),
                    route_key="runs.create",
                )
            async with database.create() as unit_of_work:
                rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT 'runs' AS source FROM runs "
                    "UNION ALL SELECT 'events' FROM run_events "
                    "UNION ALL SELECT 'idempotency' FROM api_idempotency_records",
                )
                await unit_of_work.commit()
            assert rows == []
        finally:
            await database.close()

    asyncio.run(scenario())


def test_expired_idempotency_record_allows_a_new_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        now = [NOW]
        try:
            await _insert_user(database, "member-user-00001")
            identifiers = iter(("run-identifier-0001", "run-identifier-0002"))
            service = RunService(
                SqliteRunStore(database),
                clock=lambda: now[0],
                id_factory=lambda: next(identifiers),
            )
            first = await service.create_run(
                _request(
                    "member-user-00001",
                    request_id="request-identifier-1",
                    idempotency_key="expiring-once",
                ),
                route_key="runs.create",
            )
            now[0] += timedelta(hours=25)
            second = await service.create_run(
                _request(
                    "member-user-00001",
                    request_id="request-identifier-2",
                    idempotency_key="expiring-once",
                ),
                route_key="runs.create",
            )
            assert not first.replayed and not second.replayed
            assert first.run.id != second.run.id
            async with database.create() as unit_of_work:
                run_count = await fetch_one(
                    unit_of_work.connection,
                    "SELECT COUNT(*) FROM runs",
                )
                record = await fetch_one(
                    unit_of_work.connection,
                    "SELECT run_id FROM api_idempotency_records",
                )
                await unit_of_work.commit()
            assert run_count is not None and int(run_count[0]) == 2
            assert record is not None and str(record["run_id"]) == second.run.id
        finally:
            await database.close()

    asyncio.run(scenario())


def test_principal_is_revalidated_against_active_stored_user(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(
                database,
                "disabled-user-0001",
                status=UserStatus.DISABLED,
            )
            await _insert_user(database, "member-user-00001")
            service = RunService(
                SqliteRunStore(database),
                clock=lambda: NOW,
                id_factory=lambda: "run-identifier-0001",
            )
            with pytest.raises(RunPrincipalUnavailableError):
                await service.create_run(
                    _request(
                        "disabled-user-0001",
                        request_id="request-identifier-1",
                        idempotency_key="disabled-once",
                    ),
                    route_key="runs.create",
                )
            with pytest.raises(RunPrincipalUnavailableError):
                await service.create_run(
                    _request(
                        "member-user-00001",
                        request_id="request-identifier-2",
                        idempotency_key="stale-role-once",
                        role=UserRole.ADMIN,
                    ),
                    route_key="runs.create",
                )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_cursor_pagination_and_owner_scope_remain_stable_during_insert(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database, "member-user-00001")
            await _insert_user(database, "member-user-00002")
            await _insert_user(database, "admin-user-00001", role=UserRole.ADMIN)
            identifiers = iter(
                (
                    "run-identifier-0001",
                    "run-identifier-0002",
                    "run-identifier-0003",
                    "run-identifier-0004",
                    "run-identifier-0005",
                )
            )
            service = RunService(
                SqliteRunStore(database),
                clock=lambda: NOW,
                id_factory=lambda: next(identifiers),
            )
            requests = (
                ("member-user-00001", "request-identifier-1", NOW + timedelta(seconds=1)),
                ("member-user-00001", "request-identifier-2", NOW + timedelta(seconds=2)),
                ("member-user-00001", "request-identifier-3", NOW + timedelta(seconds=2)),
                ("member-user-00002", "request-identifier-4", NOW + timedelta(seconds=3)),
            )
            for index, (user_id, request_id, created_at) in enumerate(requests, start=1):
                await service.create_run(
                    _request(
                        user_id,
                        request_id=request_id,
                        idempotency_key=f"page-once-{index}",
                        created_at=created_at,
                    ),
                    route_key="runs.create",
                )

            admin = _actor("admin-user-00001", role=UserRole.ADMIN)
            first_page = await service.list_runs(actor=admin, query=RunListQuery(limit=2))
            assert [item.id for item in first_page.items] == [
                "run-identifier-0004",
                "run-identifier-0003",
            ]
            assert first_page.next_cursor is not None

            await service.create_run(
                _request(
                    "member-user-00002",
                    request_id="request-identifier-5",
                    idempotency_key="page-once-5",
                    created_at=NOW + timedelta(seconds=4),
                ),
                route_key="runs.create",
            )
            second_page = await service.list_runs(
                actor=admin,
                query=RunListQuery(limit=2, cursor=first_page.next_cursor),
            )
            assert [item.id for item in second_page.items] == [
                "run-identifier-0002",
                "run-identifier-0001",
            ]
            assert {
                item.id for item in first_page.items + second_page.items
            } == {
                "run-identifier-0001",
                "run-identifier-0002",
                "run-identifier-0003",
                "run-identifier-0004",
            }
            assert not hasattr(first_page.items[0], "text")

            member_page = await service.list_runs(
                actor=_actor("member-user-00001"),
                query=RunListQuery(limit=10),
            )
            assert [item.id for item in member_page.items] == [
                "run-identifier-0003",
                "run-identifier-0002",
                "run-identifier-0001",
            ]
            with pytest.raises(RunNotFoundError):
                await service.get_run(
                    actor=_actor("member-user-00001"),
                    run_id="run-identifier-0004",
                )
            foreign = await service.get_run(
                actor=admin,
                run_id="run-identifier-0004",
            )
            assert foreign.request.text == "이번 주 열린 이슈를 요약해줘"
            with pytest.raises(InvalidRunCursorError):
                await service.list_runs(
                    actor=_actor("member-user-00001"),
                    query=RunListQuery(limit=2, cursor=first_page.next_cursor),
                )
            with pytest.raises(InvalidRunCursorError):
                await service.list_runs(
                    actor=admin,
                    query=RunListQuery(
                        states=(RunState.RECEIVED,),
                        limit=2,
                        cursor=first_page.next_cursor,
                    ),
                )
        finally:
            await database.close()

    asyncio.run(scenario())
