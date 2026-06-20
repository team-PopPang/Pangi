from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from pangi.config import get_settings
from pangi.domain.models import AgentJob, JobStatus
from pangi.repository import JobRepository, get_job_repository


class JobQueue(Protocol):
    """infra와 테스트에서 사용하는 job queue 계약.

    `enqueue`는 작업을 예약한다. `run`과 `cancel`은 worker 테스트와
    추후 관리자/승인 흐름에서 job을 명시적으로 제어할 때 사용한다.
    """

    async def enqueue(self, job_id: str) -> None:
        """job id를 queue에 넣어 worker가 나중에 실행하게 한다."""
        ...

    async def run(self, job_id: str) -> None:
        """지정한 job을 즉시 실행한다. 테스트와 명시적 재실행 흐름에서 사용한다."""
        ...

    async def cancel(self, job_id: str) -> None:
        """지정한 job을 취소 상태로 바꾸고 취소 progress hook을 호출한다."""
        ...


@dataclass(frozen=True)
class JobRunResult:
    stdout: str | None = None
    stderr: str | None = None


class JobExecutionTimedOut(TimeoutError):
    pass


JobRunner = Callable[[AgentJob], Awaitable[JobRunResult | None]]
JobProgressHook = Callable[[AgentJob, JobStatus, str], object]


async def _default_runner(job: AgentJob) -> JobRunResult:
    raise RuntimeError("Worktree and Codex runner are not implemented yet")


class InProcessJobQueue:
    def __init__(
        self,
        *,
        repository: JobRepository,
        runner: JobRunner = _default_runner,
        progress_hook: JobProgressHook | None = None,
        job_timeout_seconds: float | None = None,
        max_concurrency: int = 1,
        repo_concurrency: int = 1,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if repo_concurrency < 1:
            raise ValueError("repo_concurrency must be at least 1")

        self._repository = repository
        self._runner = runner
        self._progress_hook = progress_hook
        self._job_timeout_seconds = job_timeout_seconds
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._max_concurrency = max_concurrency
        self._global_semaphore = asyncio.Semaphore(max_concurrency)
        self._repo_concurrency = repo_concurrency
        self._repo_semaphores: dict[str, asyncio.Semaphore] = {}
        self._repo_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._workers:
            return
        for index in range(self._max_concurrency):
            self._workers.append(asyncio.create_task(self._worker_loop(), name=f"pangi-worker-{index + 1}"))

    async def stop(self) -> None:
        if not self._workers:
            return
        for _ in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def run(self, job_id: str) -> None:
        job = self._repository.get_job(job_id)
        if job is None:
            return

        async with self._global_semaphore:
            repo_semaphore = await self._repo_semaphore(job.repo_key)
            async with repo_semaphore:
                await self._run_locked(job_id)

    async def cancel(self, job_id: str) -> None:
        job = self._repository.update_job_status(
            job_id,
            JobStatus.CANCELLED,
            error_message="Job was cancelled",
        )
        await self._emit(job, JobStatus.CANCELLED, "작업이 취소되었습니다.")

    async def _worker_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                if job_id is None:
                    return
                await self.run(job_id)
            finally:
                self._queue.task_done()

    async def _run_locked(self, job_id: str) -> None:
        job = self._repository.get_job(job_id)
        if job is None:
            return
        if job.status == JobStatus.CANCELLED:
            await self._emit(job, JobStatus.CANCELLED, "작업이 취소되었습니다.")
            return

        running_job = self._repository.update_job_status(job_id, JobStatus.RUNNING)
        await self._emit(running_job, JobStatus.RUNNING, "팡이가 요청을 처리하고 있습니다.")

        try:
            timeout = self._job_timeout_seconds
            if timeout is None:
                timeout = get_settings().job_timeout_seconds
            await asyncio.wait_for(self._runner(running_job), timeout=timeout)
        except TimeoutError:
            timed_out_job = self._repository.update_job_status(
                job_id,
                JobStatus.TIMED_OUT,
                error_message="Job timed out",
            )
            await self._emit(timed_out_job, JobStatus.TIMED_OUT, "작업 시간이 초과되었습니다.")
            return
        except Exception as error:
            failed_job = self._repository.update_job_status(
                job_id,
                JobStatus.FAILED,
                error_message=str(error),
            )
            await self._emit(failed_job, JobStatus.FAILED, "작업이 실패했습니다.")
            return

        current_job = self._repository.get_job(job_id)
        if current_job is None:
            return
        if current_job.status == JobStatus.CANCELLED:
            await self._emit(current_job, JobStatus.CANCELLED, "작업이 취소되었습니다.")
            return

        succeeded_job = self._repository.update_job_status(job_id, JobStatus.SUCCEEDED)
        await self._emit(succeeded_job, JobStatus.SUCCEEDED, "작업이 완료되었습니다.")

    async def _repo_semaphore(self, repo_key: str) -> asyncio.Semaphore:
        async with self._repo_lock:
            semaphore = self._repo_semaphores.get(repo_key)
            if semaphore is None:
                semaphore = asyncio.Semaphore(self._repo_concurrency)
                self._repo_semaphores[repo_key] = semaphore
            return semaphore

    async def _emit(self, job: AgentJob, status: JobStatus, message: str) -> None:
        if self._progress_hook is None:
            return
        result = self._progress_hook(job, status, message)
        if inspect.isawaitable(result):
            await result


_job_queue: JobQueue | None = None


def get_job_queue() -> JobQueue:
    global _job_queue
    if _job_queue is None:
        _job_queue = InProcessJobQueue(repository=get_job_repository())
    return _job_queue


def set_job_queue(queue: JobQueue | None) -> None:
    global _job_queue
    _job_queue = queue
