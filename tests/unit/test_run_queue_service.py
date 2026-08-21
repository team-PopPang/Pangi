"""Persistent Run queue policy and process-local runtime tests."""

import asyncio
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from pangi.application.contracts.run_queue import (
    RunCancellation,
    RunClaim,
    RunQueuePolicy,
    RunRecoveryResult,
)
from pangi.application.ports.run_queue import RunQueueUnavailableError
from pangi.application.services.run_queue import RunQueueRuntime, RunQueueService
from pangi.domain.auth import UserRole
from pangi.domain.runs import Principal, PrincipalChannel, Run, RunRequest, RunState, transition_run

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _run(index: int) -> Run:
    request = RunRequest(
        request_id=f"request-identifier-{index}",
        principal=Principal(
            "member-user-00001",
            UserRole.MEMBER,
            PrincipalChannel.DASHBOARD,
        ),
        text=f"queued request {index}",
        idempotency_key=f"queue-once-{index}",
        created_at=NOW + timedelta(seconds=index),
    )
    return Run(
        id=f"run-identifier-{index:04d}",
        request=request,
        state=RunState.QUEUED,
        updated_at=request.created_at,
        revision=1,
    )


class MemoryQueueStore:
    def __init__(self, runs: tuple[Run, ...]) -> None:
        self.queued = deque(runs)
        self.running: dict[str, Run] = {}
        self.recovery_calls = 0
        self.heartbeat_calls: list[str] = []
        self.abandoned: list[tuple[str, str]] = []

    async def enqueue(
        self,
        *,
        run_id: str,
        expected_revision: int,
        at: datetime,
    ) -> Run:
        raise AssertionError("the runtime test preloads queued Runs")

    async def claim_next(
        self,
        *,
        worker_id: str,
        at: datetime,
        lease_expires_at: datetime,
    ) -> RunClaim | None:
        if not self.queued:
            return None
        queued = self.queued.popleft()
        claimed = replace(
            transition_run(queued, RunState.RUNNING, at=at),
            worker_id=worker_id,
            heartbeat_at=at,
            lease_expires_at=lease_expires_at,
        )
        self.running[claimed.id] = claimed
        return RunClaim(claimed)

    async def heartbeat(
        self,
        *,
        run_id: str,
        worker_id: str,
        at: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        self.heartbeat_calls.append(run_id)
        run = self.running.get(run_id)
        return run is not None and run.worker_id == worker_id

    async def cancel(self, *, run_id: str, at: datetime) -> RunCancellation:
        current = self.running.pop(run_id)
        cancelled = replace(
            transition_run(current, RunState.CANCELLED, at=at),
            worker_id=None,
            heartbeat_at=None,
            lease_expires_at=None,
        )
        return RunCancellation(cancelled, True)

    async def recover_expired(self, *, at: datetime) -> RunRecoveryResult:
        self.recovery_calls += 1
        return RunRecoveryResult()

    async def abandon_claim(
        self,
        *,
        run_id: str,
        worker_id: str,
        at: datetime,
        reason: str,
    ) -> RunRecoveryResult:
        current = self.running.get(run_id)
        if current is not None and current.worker_id == worker_id:
            self.running.pop(run_id)
            self.abandoned.append((run_id, reason))
        return RunRecoveryResult()


class BlockingHandler:
    def __init__(self) -> None:
        self.gates: dict[str, asyncio.Event] = {}
        self.started: list[str] = []
        self.active = 0
        self.peak = 0

    async def execute(self, claim: RunClaim) -> None:
        gate = self.gates.setdefault(claim.run_id, asyncio.Event())
        self.started.append(claim.run_id)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await gate.wait()
        finally:
            self.active -= 1


async def _wait_until(predicate: object) -> None:
    for _attempt in range(200):
        if callable(predicate) and predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("async queue condition was not reached")


def test_queue_policy_requires_bounded_concurrency_and_live_heartbeat() -> None:
    policy = RunQueuePolicy(
        max_concurrent_runs=2,
        lease_duration=timedelta(seconds=30),
        heartbeat_interval=timedelta(seconds=10),
    )
    assert policy.max_concurrent_runs == 2
    with pytest.raises(ValueError, match="between 1 and 64"):
        RunQueuePolicy(0, timedelta(seconds=30), timedelta(seconds=10))
    with pytest.raises(ValueError, match="shorter"):
        RunQueuePolicy(1, timedelta(seconds=10), timedelta(seconds=10))


def test_runtime_recovers_once_limits_concurrency_and_cancels_active_run() -> None:
    async def scenario() -> None:
        store = MemoryQueueStore((_run(1), _run(2), _run(3)))
        policy = RunQueuePolicy(
            max_concurrent_runs=2,
            lease_duration=timedelta(seconds=30),
            heartbeat_interval=timedelta(seconds=10),
        )
        service = RunQueueService(store, policy, clock=lambda: NOW)
        handler = BlockingHandler()
        runtime = RunQueueRuntime(
            service,
            handler,
            worker_id_factory=lambda: "worker-identifier-0001",
        )

        with pytest.raises(RunQueueUnavailableError):
            runtime.wake()
        await runtime.start()
        await runtime.start()
        assert runtime.ready
        await _wait_until(lambda: len(handler.started) == 2)
        assert handler.peak == 2
        assert store.recovery_calls == 1
        first, second = handler.started

        handler.gates[first].set()
        await _wait_until(lambda: len(handler.started) == 3)
        assert handler.peak == 2

        cancelled = await runtime.cancel(run_id=second)
        assert cancelled.changed
        await _wait_until(lambda: second not in runtime.active_run_ids)

        third = handler.started[-1]
        handler.gates[third].set()
        await _wait_until(lambda: not runtime.active_run_ids)
        await runtime.close()
        await runtime.close()

        assert not runtime.started
        assert not runtime.ready
        assert first in {run_id for run_id, _reason in store.abandoned}
        assert third in {run_id for run_id, _reason in store.abandoned}
        assert all(reason == "handler_returned" for _run_id, reason in store.abandoned)

    asyncio.run(scenario())


def test_dispatcher_failure_marks_runtime_not_ready() -> None:
    class FailingStore(MemoryQueueStore):
        async def claim_next(self, **_kwargs: object) -> RunClaim | None:
            raise RuntimeError("queue read failed")

    async def scenario() -> None:
        store = FailingStore(())
        service = RunQueueService(
            store,
            RunQueuePolicy(1, timedelta(seconds=30), timedelta(seconds=10)),
            clock=lambda: NOW,
        )
        runtime = RunQueueRuntime(
            service,
            BlockingHandler(),
            worker_id_factory=lambda: "worker-identifier-0001",
        )

        await runtime.start()
        await _wait_until(lambda: not runtime.ready)

        with pytest.raises(RunQueueUnavailableError):
            runtime.wake()
        await runtime.close()

    asyncio.run(scenario())


def test_queue_service_rejects_a_naive_clock() -> None:
    store = MemoryQueueStore(())
    service = RunQueueService(
        store,
        RunQueuePolicy(1, timedelta(seconds=30), timedelta(seconds=10)),
        clock=lambda: datetime(2030, 1, 1),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(service.recover_expired())
