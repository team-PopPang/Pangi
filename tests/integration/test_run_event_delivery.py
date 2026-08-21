"""Run Event visibility, cancellation ownership, and Queue metric integration tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.run_events import SqliteRunEventStore
from pangi.adapters.outbound.persistence.sqlite.runs import (
    SqliteRunQueueStore,
    SqliteRunStore,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.run_events import RunEventDraft
from pangi.application.contracts.run_queue import RunQueuePolicy
from pangi.application.ports.auth import PermissionDeniedError
from pangi.application.ports.run_events import RunEventNotFoundError
from pangi.application.ports.runs import RunNotFoundError
from pangi.application.services.run_events import (
    RunCancellationService,
    RunEventService,
    RunQueueMetricService,
)
from pangi.application.services.run_queue import RunQueueService
from pangi.application.services.runs import RunService
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.runs import (
    EventVisibility,
    Principal,
    PrincipalChannel,
    Run,
    RunRequest,
    RunState,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class QueueNotifier:
    def __init__(self) -> None:
        self.cancelled_run_ids: list[str] = []
        self.ready = True

    def wake(self) -> None:
        raise AssertionError("cancellation must not wake the Queue")

    def cancel_active(self, run_id: str) -> None:
        self.cancelled_run_ids.append(run_id)


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


async def _insert_user(
    database: SqliteDatabase,
    user_id: str,
    *,
    role: UserRole = UserRole.MEMBER,
) -> None:
    async with database.create() as unit_of_work:
        await unit_of_work.connection.execute(
            "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'active', ?, ?)",
            (user_id, user_id, role.value, NOW.isoformat(), NOW.isoformat()),
        )
        await unit_of_work.commit()


def _actor(user_id: str, *, role: UserRole = UserRole.MEMBER) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(user_id, user_id, role, UserStatus.ACTIVE)


def _request(user_id: str, index: int) -> RunRequest:
    return RunRequest(
        request_id=f"request-identifier-{index}",
        principal=Principal(user_id, UserRole.MEMBER, PrincipalChannel.DASHBOARD),
        text=f"event delivery request {index}",
        idempotency_key=f"event-delivery-{index}",
        created_at=NOW + timedelta(seconds=index),
    )


async def _create_run(database: SqliteDatabase, user_id: str, index: int) -> Run:
    service = RunService(
        SqliteRunStore(database),
        clock=lambda: NOW,
        id_factory=lambda: f"run-identifier-{index:04d}",
    )
    result = await service.create_run(
        _request(user_id, index),
        route_key="runs.create",
    )
    return result.run


def test_event_indexes_are_atomic_and_http_visibility_never_exposes_internal(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database, "member-user-00001")
            await _insert_user(database, "member-user-00002")
            await _insert_user(database, "admin-user-000001", role=UserRole.ADMIN)
            run = await _create_run(database, "member-user-00001", 1)
            store = SqliteRunEventStore(database)
            visibilities = (
                EventVisibility.PUBLIC,
                EventVisibility.ADMIN,
                EventVisibility.INTERNAL,
            )
            appended = await asyncio.gather(
                *(
                    store.append_event(
                        RunEventDraft(
                            run_id=run.id,
                            type=f"run.fixture_{index}",
                            visibility=visibilities[index % len(visibilities)],
                            created_at=NOW + timedelta(seconds=10 + index),
                            message=f"fixture {index}",
                            attributes={"sequence": index},
                        )
                    )
                    for index in range(20)
                )
            )
            assert sorted(event.index for event in appended) == list(range(2, 22))

            service = RunEventService(store)
            owner_page = await service.list_events(
                actor=_actor("member-user-00001"),
                run_id=run.id,
                after_index=0,
                limit=100,
            )
            assert owner_page.items
            assert all(
                event.visibility is EventVisibility.PUBLIC for event in owner_page.items
            )
            assert "run.received" in {event.type for event in owner_page.items}

            admin_page = await service.list_events(
                actor=_actor("admin-user-000001", role=UserRole.ADMIN),
                run_id=run.id,
                after_index=0,
                limit=100,
            )
            assert {event.visibility for event in admin_page.items} == {
                EventVisibility.PUBLIC,
                EventVisibility.ADMIN,
            }
            assert all(
                event.visibility is not EventVisibility.INTERNAL
                for event in admin_page.items
            )

            internal = await store.read_events(
                run_id=run.id,
                owner_user_id=None,
                visibilities=(EventVisibility.INTERNAL,),
                after_index=0,
                limit=100,
            )
            assert internal is not None and internal.items
            assert all(
                event.visibility is EventVisibility.INTERNAL for event in internal.items
            )

            with pytest.raises(RunEventNotFoundError):
                await service.list_events(
                    actor=_actor("member-user-00002"),
                    run_id=run.id,
                    after_index=0,
                    limit=100,
                )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_owner_cancel_and_admin_queue_metrics_are_enforced(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database, "member-user-00001")
            await _insert_user(database, "member-user-00002")
            await _insert_user(database, "admin-user-000001", role=UserRole.ADMIN)
            first = await _create_run(database, "member-user-00001", 1)
            second = await _create_run(database, "member-user-00001", 2)
            third = await _create_run(database, "member-user-00001", 3)
            clock = MutableClock(NOW + timedelta(seconds=10))
            queue = RunQueueService(
                SqliteRunQueueStore(database),
                RunQueuePolicy(2, timedelta(seconds=10), timedelta(seconds=3)),
                clock=clock,
            )
            await queue.enqueue(run_id=first.id, expected_revision=first.revision)
            clock.current += timedelta(seconds=1)
            await queue.enqueue(run_id=second.id, expected_revision=second.revision)
            clock.current += timedelta(seconds=1)
            await queue.enqueue(run_id=third.id, expected_revision=third.revision)

            notifier = QueueNotifier()
            cancellation = RunCancellationService(
                SqliteRunStore(database),
                SqliteRunQueueStore(database),
                runtime_notifier=notifier,
                clock=lambda: NOW + timedelta(seconds=13),
            )
            with pytest.raises(RunNotFoundError):
                await cancellation.cancel_run(
                    actor=_actor("member-user-00002"),
                    run_id=second.id,
                )
            cancelled = await cancellation.cancel_run(
                actor=_actor("member-user-00001"),
                run_id=second.id,
            )
            assert cancelled.changed
            assert cancelled.run.state is RunState.CANCELLED
            assert notifier.cancelled_run_ids == [second.id]

            clock.current = NOW + timedelta(seconds=14)
            claim = await queue.claim_next(worker_id="worker-identifier-0001")
            assert claim is not None and claim.run_id == first.id
            metric_at = NOW + timedelta(seconds=25)
            metrics = RunQueueMetricService(
                SqliteRunEventStore(database),
                clock=lambda: metric_at,
            )
            with pytest.raises(PermissionDeniedError):
                await metrics.queue_metrics(actor=_actor("member-user-00001"))
            snapshot = await metrics.queue_metrics(
                actor=_actor("admin-user-000001", role=UserRole.ADMIN)
            )
            assert snapshot.queue_depth == 1
            assert snapshot.running_count == 1
            assert snapshot.expired_lease_count == 1
            assert snapshot.oldest_queued_at == NOW + timedelta(seconds=12)
            assert snapshot.oldest_queued_age_seconds == 13
        finally:
            await database.close()

    asyncio.run(scenario())
