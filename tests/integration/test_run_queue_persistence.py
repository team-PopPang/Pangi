"""SQLite Run queue claim, lease, cancellation, and recovery tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.runs import (
    SqliteRunQueueStore,
    SqliteRunStore,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.run_queue import RunQueuePolicy
from pangi.application.services.run_queue import RunQueueService
from pangi.application.services.runs import RunService
from pangi.domain.auth import UserRole
from pangi.domain.runs import Principal, PrincipalChannel, Run, RunRequest, RunState

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


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


async def _insert_user(database: SqliteDatabase) -> None:
    async with database.create() as unit_of_work:
        timestamp = NOW.isoformat()
        await unit_of_work.connection.execute(
            "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
            "VALUES ('member-user-00001', 'Member', 'member', 'active', ?, ?)",
            (timestamp, timestamp),
        )
        await unit_of_work.commit()


def _request(index: int) -> RunRequest:
    return RunRequest(
        request_id=f"request-identifier-{index}",
        principal=Principal(
            "member-user-00001",
            UserRole.MEMBER,
            PrincipalChannel.DASHBOARD,
        ),
        text=f"persistent queue request {index}",
        idempotency_key=f"persistent-queue-{index}",
        created_at=NOW + timedelta(seconds=index),
    )


async def _create_runs(database: SqliteDatabase, count: int) -> tuple[Run, ...]:
    identifiers = iter(f"run-identifier-{index:04d}" for index in range(1, count + 1))
    service = RunService(
        SqliteRunStore(database),
        clock=lambda: NOW,
        id_factory=lambda: next(identifiers),
    )
    created: list[Run] = []
    for index in range(1, count + 1):
        result = await service.create_run(_request(index), route_key="runs.create")
        created.append(result.run)
    return tuple(created)


def test_queue_claim_is_fifo_unique_and_cancellation_rejects_stale_worker(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            runs = await _create_runs(database, 3)
            clock = MutableClock(NOW + timedelta(seconds=10))
            queue = RunQueueService(
                SqliteRunQueueStore(database),
                RunQueuePolicy(2, timedelta(seconds=30), timedelta(seconds=10)),
                clock=clock,
            )
            for run in runs:
                await queue.enqueue(run_id=run.id, expected_revision=run.revision)
                clock.current += timedelta(seconds=1)

            first, second = await asyncio.gather(
                queue.claim_next(worker_id="worker-identifier-0001"),
                queue.claim_next(worker_id="worker-identifier-0002"),
            )
            assert first is not None and second is not None
            assert {first.run_id, second.run_id} == {
                "run-identifier-0001",
                "run-identifier-0002",
            }
            assert first.run_id != second.run_id

            queued_cancel = await queue.cancel(run_id="run-identifier-0003")
            assert queued_cancel.changed
            assert queued_cancel.run.state is RunState.CANCELLED
            assert await queue.claim_next(worker_id="worker-identifier-0003") is None

            clock.current = NOW + timedelta(seconds=20)
            assert not await SqliteRunQueueStore(database).heartbeat(
                run_id=first.run_id,
                worker_id="stale-worker-0001",
                at=clock.current,
                lease_expires_at=clock.current + timedelta(seconds=30),
            )
            assert await queue.heartbeat(first)

            running_cancel = await queue.cancel(run_id=first.run_id)
            replayed_cancel = await queue.cancel(run_id=first.run_id)
            assert running_cancel.changed
            assert not replayed_cancel.changed
            assert not await queue.heartbeat(first)
            await queue.cancel(run_id=second.run_id)

            async with database.create() as unit_of_work:
                events = await fetch_all(
                    unit_of_work.connection,
                    "SELECT event_index, type FROM run_events "
                    "WHERE run_id = ? ORDER BY event_index",
                    (first.run_id,),
                )
                await unit_of_work.commit()
            assert [(int(row["event_index"]), str(row["type"])) for row in events] == [
                (1, "run.received"),
                (2, "run.queued"),
                (3, "run.running"),
                (4, "run.cancelled"),
            ]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_startup_recovery_requeues_safe_work_and_fails_non_idempotent_step(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            runs = await _create_runs(database, 3)
            clock = MutableClock(NOW + timedelta(seconds=10))
            queue = RunQueueService(
                SqliteRunQueueStore(database),
                RunQueuePolicy(3, timedelta(seconds=20), timedelta(seconds=5)),
                clock=clock,
            )
            for run in runs:
                await queue.enqueue(run_id=run.id, expected_revision=run.revision)

            first = await queue.claim_next(worker_id="worker-identifier-0001")
            second = await queue.claim_next(worker_id="worker-identifier-0002")
            assert first is not None and second is not None
            clock.current = NOW + timedelta(seconds=20)
            third = await queue.claim_next(worker_id="worker-identifier-0003")
            assert third is not None

            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "INSERT INTO run_steps "
                    "(id, run_id, node_id, type, state, requirement, idempotent, attempt, "
                    "depends_on_json, created_at, updated_at, started_at) "
                    "VALUES ('step-identifier-0001', ?, 'collect', 'tool', 'running', "
                    "'required', 1, 1, '[]', ?, ?, ?)",
                    (
                        first.run_id,
                        NOW.isoformat(),
                        NOW.isoformat(),
                        NOW.isoformat(),
                    ),
                )
                await unit_of_work.connection.execute(
                    "INSERT INTO run_steps "
                    "(id, run_id, node_id, type, state, requirement, idempotent, attempt, "
                    "depends_on_json, created_at, updated_at, started_at) "
                    "VALUES ('step-identifier-0002', ?, 'publish', 'tool', 'running', "
                    "'required', 0, 1, '[]', ?, ?, ?)",
                    (
                        second.run_id,
                        NOW.isoformat(),
                        NOW.isoformat(),
                        NOW.isoformat(),
                    ),
                )
                await unit_of_work.commit()

            clock.current = NOW + timedelta(seconds=31)
            recovered = await queue.recover_expired()
            assert recovered.requeued_run_ids == (first.run_id,)
            assert recovered.failed_run_ids == (second.run_id,)

            async with database.create() as unit_of_work:
                run_rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT id, state, revision, worker_id, lease_expires_at, error_code "
                    "FROM runs ORDER BY id",
                )
                steps = await fetch_all(
                    unit_of_work.connection,
                    "SELECT run_id, state, error_code, finished_at "
                    "FROM run_steps ORDER BY run_id",
                )
                event_rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT run_id, event_index, type FROM run_events "
                    "WHERE run_id IN (?, ?) ORDER BY run_id, event_index",
                    (first.run_id, second.run_id),
                )
                await unit_of_work.commit()
            states = {str(row["id"]): str(row["state"]) for row in run_rows}
            assert states == {
                first.run_id: "queued",
                second.run_id: "failed",
                third.run_id: "running",
            }
            failed_row = next(row for row in run_rows if str(row["id"]) == second.run_id)
            assert str(failed_row["error_code"]) == "non_idempotent_recovery"
            assert failed_row["worker_id"] is None
            assert failed_row["lease_expires_at"] is None
            assert [
                (
                    str(step["run_id"]),
                    str(step["state"]),
                    step["error_code"],
                    step["finished_at"],
                )
                for step in steps
            ] == [
                (first.run_id, "interrupted", None, None),
                (
                    second.run_id,
                    "failed",
                    "non_idempotent_recovery",
                    clock.current.isoformat(),
                ),
            ]
            grouped_events = [
                (str(row["run_id"]), int(row["event_index"]), str(row["type"]))
                for row in event_rows
            ]
            assert grouped_events == [
                (first.run_id, 1, "run.received"),
                (first.run_id, 2, "run.queued"),
                (first.run_id, 3, "run.running"),
                (first.run_id, 4, "run.interrupted"),
                (first.run_id, 5, "run.queued"),
                (second.run_id, 1, "run.received"),
                (second.run_id, 2, "run.queued"),
                (second.run_id, 3, "run.running"),
                (second.run_id, 4, "run.interrupted"),
                (second.run_id, 5, "run.failed"),
            ]

            await database.close()
            await database.start()
            persisted = await SqliteRunStore(database).get_run(
                run_id=first.run_id,
                owner_user_id=None,
            )
            assert persisted is not None
            assert persisted.state is RunState.QUEUED
            assert persisted.revision == 4
        finally:
            await database.close()

    asyncio.run(scenario())
