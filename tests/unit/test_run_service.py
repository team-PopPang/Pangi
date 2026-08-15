"""Run application-service contracts without concrete adapters."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.runs import (
    RunCreateRecord,
    RunCreation,
    RunListQuery,
    RunStoreQuery,
    RunSummary,
)
from pangi.application.ports.runs import InvalidRunCursorError, RunNotFoundError
from pangi.application.services.runs import RunService, request_fingerprint
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.runs import Principal, PrincipalChannel, Run, RunRequest, RunState

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class MemoryRunStore:
    def __init__(self) -> None:
        self.created: RunCreateRecord | None = None
        self.run: Run | None = None
        self.queries: list[RunStoreQuery] = []
        self.summary_responses: list[tuple[RunSummary, ...]] = []

    async def create_or_replay(self, record: RunCreateRecord) -> RunCreation:
        self.created = record
        self.run = record.run
        return RunCreation(record.run, False)

    async def get_run(self, *, run_id: str, owner_user_id: str | None) -> Run | None:
        if self.run is None or self.run.id != run_id:
            return None
        if owner_user_id is not None and self.run.request.principal.user_id != owner_user_id:
            return None
        return self.run

    async def list_run_summaries(self, query: RunStoreQuery) -> tuple[RunSummary, ...]:
        self.queries.append(query)
        return self.summary_responses.pop(0)


def _request(
    *,
    request_id: str = "request-identifier-1",
    idempotency_key: str = "request-once-1",
    text: str = "이번 주 열린 이슈를 요약해줘",
    created_at: datetime = NOW,
) -> RunRequest:
    return RunRequest(
        request_id=request_id,
        principal=Principal(
            "member-user-00001",
            UserRole.MEMBER,
            PrincipalChannel.DASHBOARD,
        ),
        text=text,
        idempotency_key=idempotency_key,
        created_at=created_at,
        thread_key="thread-key-1",
    )


def _actor(
    user_id: str = "member-user-00001",
    *,
    role: UserRole = UserRole.MEMBER,
    status: UserStatus = UserStatus.ACTIVE,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(user_id, "Actor", role, status)


def _summary(run_id: str, created_at: datetime) -> RunSummary:
    return RunSummary(
        id=run_id,
        request_id=f"request-{run_id}",
        principal_id="member-user-00001",
        trigger=PrincipalChannel.DASHBOARD,
        state=RunState.RECEIVED,
        mode=None,
        skill_version_id=None,
        revision=0,
        created_at=created_at,
        updated_at=created_at,
        started_at=None,
        finished_at=None,
        warning_count=0,
        error_code=None,
    )


def test_create_run_builds_safe_atomic_record_and_semantic_fingerprint() -> None:
    store = MemoryRunStore()
    service = RunService(
        store,
        clock=lambda: NOW + timedelta(minutes=1),
        id_factory=lambda: "run-identifier-0001",
    )
    request = _request()

    created = asyncio.run(service.create_run(request, route_key="runs.create"))

    assert created.run.id == "run-identifier-0001"
    assert created.run.state is RunState.RECEIVED
    assert not created.replayed
    assert store.created is not None
    assert store.created.first_event.run_id == created.run.id
    assert store.created.first_event.index == 1
    assert store.created.first_event.type == "run.received"
    assert store.created.first_event.attributes == {"trigger": "dashboard"}
    assert store.created.expires_at - store.created.recorded_at == timedelta(hours=24)
    assert len(store.created.request_fingerprint) == 64

    transport_retry = _request(
        request_id="request-identifier-2",
        idempotency_key="a-different-transport-key",
        created_at=NOW + timedelta(minutes=5),
    )
    changed_request = _request(text="다른 요청")
    assert request_fingerprint(transport_retry) == request_fingerprint(request)
    assert request_fingerprint(changed_request) != request_fingerprint(request)
    with pytest.raises(ValueError, match="route_key"):
        asyncio.run(service.create_run(request, route_key="Runs Create"))


def test_cursor_is_bound_to_actor_filters_and_stable_keyset_position() -> None:
    store = MemoryRunStore()
    first = _summary("run-identifier-0003", NOW + timedelta(seconds=3))
    second = _summary("run-identifier-0002", NOW + timedelta(seconds=2))
    third = _summary("run-identifier-0001", NOW + timedelta(seconds=1))
    store.summary_responses = [(first, second, third), (third,)]
    service = RunService(store)
    actor = _actor()
    query = RunListQuery(limit=2)

    first_page = asyncio.run(service.list_runs(actor=actor, query=query))

    assert first_page.items == (first, second)
    assert first_page.next_cursor is not None
    assert store.queries[0].owner_user_id == actor.user_id
    assert store.queries[0].limit == 3
    second_page = asyncio.run(
        service.list_runs(
            actor=actor,
            query=RunListQuery(limit=2, cursor=first_page.next_cursor),
        )
    )
    assert second_page.items == (third,)
    assert second_page.next_cursor is None
    assert store.queries[1].after is not None
    assert store.queries[1].after.run_id == second.id
    assert store.queries[1].after.created_at == second.created_at

    with pytest.raises(InvalidRunCursorError):
        asyncio.run(
            service.list_runs(
                actor=_actor("another-user-0001"),
                query=RunListQuery(limit=2, cursor=first_page.next_cursor),
            )
        )
    with pytest.raises(InvalidRunCursorError):
        asyncio.run(
            service.list_runs(
                actor=actor,
                query=RunListQuery(
                    states=(RunState.RECEIVED,),
                    limit=2,
                    cursor=first_page.next_cursor,
                ),
            )
        )
    assert first_page.next_cursor is not None
    replacement = "A" if first_page.next_cursor[-1] != "A" else "B"
    tampered = f"{first_page.next_cursor[:-1]}{replacement}"
    with pytest.raises(InvalidRunCursorError):
        asyncio.run(
            service.list_runs(
                actor=actor,
                query=RunListQuery(limit=2, cursor=tampered),
            )
        )


def test_owner_scope_hides_foreign_or_disabled_run_existence() -> None:
    store = MemoryRunStore()
    service = RunService(store, id_factory=lambda: "run-identifier-0001")
    asyncio.run(service.create_run(_request(), route_key="runs.create"))

    own = asyncio.run(
        service.get_run(actor=_actor(), run_id="run-identifier-0001")
    )
    assert own.id == "run-identifier-0001"
    with pytest.raises(RunNotFoundError):
        asyncio.run(
            service.get_run(
                actor=_actor("another-user-0001"),
                run_id="run-identifier-0001",
            )
        )
    admin = asyncio.run(
        service.get_run(
            actor=_actor("admin-user-00001", role=UserRole.ADMIN),
            run_id="run-identifier-0001",
        )
    )
    assert admin.id == own.id
    with pytest.raises(RunNotFoundError):
        asyncio.run(
            service.list_runs(
                actor=_actor(status=UserStatus.DISABLED),
                query=RunListQuery(),
            )
        )


def test_run_list_query_rejects_mutable_or_unbounded_inputs() -> None:
    with pytest.raises(ValueError, match="immutable"):
        RunListQuery(states=[RunState.RECEIVED])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="limit"):
        RunListQuery(limit=0)
    with pytest.raises(ValueError, match="cursor"):
        RunListQuery(cursor="x" * 1025)
