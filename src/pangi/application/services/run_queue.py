"""Persistent queue use cases and process-local worker coordination."""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pangi.application.contracts.run_queue import (
    RunCancellation,
    RunClaim,
    RunQueuePolicy,
    RunRecoveryResult,
)
from pangi.application.ports.run_queue import RunExecutionHandler, RunQueueStore
from pangi.domain.runs import Run

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]
Sleeper = Callable[[float], Awaitable[None]]

_REASON = re.compile(r"^[a-z][a-z0-9_]{0,119}$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _identifier() -> str:
    return uuid.uuid4().hex


class RunQueueService:
    """Apply queue timing policy before calling persistent storage."""

    def __init__(
        self,
        store: RunQueueStore,
        policy: RunQueuePolicy,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._store = store
        self.policy = policy
        self._clock = clock

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("queue clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    async def enqueue(self, *, run_id: str, expected_revision: int) -> Run:
        return await self._store.enqueue(
            run_id=run_id,
            expected_revision=expected_revision,
            at=self._now(),
        )

    async def claim_next(self, *, worker_id: str) -> RunClaim | None:
        at = self._now()
        return await self._store.claim_next(
            worker_id=worker_id,
            at=at,
            lease_expires_at=at + self.policy.lease_duration,
        )

    async def heartbeat(self, claim: RunClaim) -> bool:
        at = self._now()
        return await self._store.heartbeat(
            run_id=claim.run_id,
            worker_id=claim.worker_id,
            at=at,
            lease_expires_at=at + self.policy.lease_duration,
        )

    async def cancel(self, *, run_id: str) -> RunCancellation:
        return await self._store.cancel(run_id=run_id, at=self._now())

    async def recover_expired(self) -> RunRecoveryResult:
        return await self._store.recover_expired(at=self._now())

    async def abandon_claim(
        self,
        claim: RunClaim,
        *,
        reason: str,
    ) -> RunRecoveryResult:
        if _REASON.fullmatch(reason) is None:
            raise ValueError("queue recovery reason must use a lowercase identifier")
        return await self._store.abandon_claim(
            run_id=claim.run_id,
            worker_id=claim.worker_id,
            at=self._now(),
            reason=reason,
        )


class RunQueueRuntime:
    """Wake a single-process dispatcher and bound active Run handlers."""

    def __init__(
        self,
        service: RunQueueService,
        handler: RunExecutionHandler,
        *,
        worker_id_factory: IdFactory = _identifier,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        worker_id = worker_id_factory()
        if not 16 <= len(worker_id) <= 64 or worker_id.strip() != worker_id:
            raise ValueError("worker_id must contain 16-64 non-padding characters")
        self._service = service
        self._handler = handler
        self._worker_id = worker_id
        self._sleeper = sleeper
        self._wake = asyncio.Event()
        self._lifecycle_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(service.policy.max_concurrent_runs)
        self._dispatcher: asyncio.Task[None] | None = None
        self._active: dict[str, tuple[RunClaim, asyncio.Task[None]]] = {}
        self._started = False
        self._stopping = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def active_run_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            await self._service.recover_expired()
            self._stopping = False
            self._started = True
            self._dispatcher = asyncio.create_task(
                self._dispatch(),
                name="pangi-run-queue-dispatcher",
            )
            self._wake.set()

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if not self._started:
                return
            self._stopping = True
            dispatcher = self._dispatcher
            self._dispatcher = None
            if dispatcher is not None:
                dispatcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await dispatcher
            tasks = tuple(task for _claim, task in self._active.values())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._active.clear()
            self._started = False
            self._stopping = False
            self._wake.clear()

    async def enqueue(self, *, run_id: str, expected_revision: int) -> Run:
        run = await self._service.enqueue(
            run_id=run_id,
            expected_revision=expected_revision,
        )
        self._wake.set()
        return run

    async def cancel(self, *, run_id: str) -> RunCancellation:
        result = await self._service.cancel(run_id=run_id)
        active = self._active.get(run_id)
        if result.changed and active is not None:
            active[1].cancel()
        return result

    async def _dispatch(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while not self._stopping:
                acquired = False
                try:
                    await self._semaphore.acquire()
                    acquired = True
                    claim = await self._service.claim_next(worker_id=self._worker_id)
                    if claim is None:
                        self._semaphore.release()
                        acquired = False
                        break
                    task = asyncio.create_task(
                        self._execute_and_release(claim),
                        name=f"pangi-run-{claim.run_id}",
                    )
                    task.add_done_callback(self._consume_task_result)
                    self._active[claim.run_id] = (claim, task)
                    acquired = False
                finally:
                    if acquired:
                        self._semaphore.release()

    @staticmethod
    def _consume_task_result(future: asyncio.Future[None]) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            future.exception()

    async def _execute_and_release(self, claim: RunClaim) -> None:
        try:
            await self._execute_claim(claim)
        finally:
            self._active.pop(claim.run_id, None)
            self._semaphore.release()
            if self._started and not self._stopping:
                self._wake.set()

    async def _execute_claim(self, claim: RunClaim) -> None:
        current = asyncio.current_task()
        if current is None:  # pragma: no cover - asyncio always supplies a current task
            raise RuntimeError("Run execution requires an asyncio Task")
        heartbeat = asyncio.create_task(
            self._maintain_lease(claim, current),
            name=f"pangi-heartbeat-{claim.run_id}",
        )
        try:
            await self._handler.execute(claim)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await asyncio.shield(
                    self._service.abandon_claim(claim, reason="worker_cancelled")
                )
            raise
        except Exception:
            await self._service.abandon_claim(claim, reason="handler_failed")
        else:
            await self._service.abandon_claim(claim, reason="handler_returned")
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _maintain_lease(
        self,
        claim: RunClaim,
        execution_task: asyncio.Task[None],
    ) -> None:
        interval = self._service.policy.heartbeat_interval.total_seconds()
        while True:
            await self._sleeper(interval)
            if not await self._service.heartbeat(claim):
                execution_task.cancel()
                return
