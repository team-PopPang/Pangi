import asyncio

from pangi.domain import JobStatus
from pangi.infra.queue import InProcessJobQueue, JobRunResult
from pangi.repository import SQLiteJobRepository


def make_job(repository, *, event_id="Ev123", repo_key="PopPang-iOS"):
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts=event_id)
    return repository.create_job(
        event_id=event_id,
        slack_thread=thread,
        codex_session_id=None,
        requester_user_id="U123",
        prompt="분석해줘",
        repo_key=repo_key,
    )


async def wait_for_status(repository, job_id, expected_status, *, attempts=50):
    for _ in range(attempts):
        job = repository.get_job(job_id)
        if job is not None and job.status == expected_status:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not reach {expected_status}")


def test_worker_runs_queued_job_to_success(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        job = make_job(repository)
        progress = []

        async def runner(_job):
            return JobRunResult(stdout="ok")

        async def hook(_job, status, message):
            progress.append((status, message))

        queue = InProcessJobQueue(
            repository=repository,
            runner=runner,
            progress_hook=hook,
            job_timeout_seconds=1,
        )

        await queue.run(job.id)

        updated = repository.get_job(job.id)
        assert updated.status == JobStatus.SUCCEEDED
        assert progress[0][0] == JobStatus.RUNNING
        assert progress[-1][0] == JobStatus.SUCCEEDED

    asyncio.run(scenario())


def test_worker_records_failure(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        job = make_job(repository)

        async def runner(_job):
            raise RuntimeError("boom")

        queue = InProcessJobQueue(
            repository=repository,
            runner=runner,
            job_timeout_seconds=1,
        )

        await queue.run(job.id)

        updated = repository.get_job(job.id)
        assert updated.status == JobStatus.FAILED
        assert updated.error_message == "boom"

    asyncio.run(scenario())


def test_worker_records_timeout(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        job = make_job(repository)

        async def runner(_job):
            await asyncio.sleep(1)

        queue = InProcessJobQueue(
            repository=repository,
            runner=runner,
            job_timeout_seconds=0.01,
        )

        await queue.run(job.id)

        updated = repository.get_job(job.id)
        assert updated.status == JobStatus.TIMED_OUT

    asyncio.run(scenario())


def test_worker_lifecycle_runs_enqueued_jobs(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        job = make_job(repository)

        async def runner(_job):
            return None

        queue = InProcessJobQueue(
            repository=repository,
            runner=runner,
            job_timeout_seconds=1,
        )
        await queue.start()
        try:
            await queue.enqueue(job.id)
            updated = await wait_for_status(repository, job.id, JobStatus.SUCCEEDED)
        finally:
            await queue.stop()

        assert updated.status == JobStatus.SUCCEEDED

    asyncio.run(scenario())


def test_worker_limits_same_repo_concurrency(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        first = make_job(repository, event_id="Ev123")
        second = make_job(repository, event_id="Ev124")
        active = 0
        max_active = 0

        async def runner(_job):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1

        queue = InProcessJobQueue(
            repository=repository,
            runner=runner,
            job_timeout_seconds=1,
            max_concurrency=2,
            repo_concurrency=1,
        )

        await asyncio.gather(queue.run(first.id), queue.run(second.id))

        assert repository.get_job(first.id).status == JobStatus.SUCCEEDED
        assert repository.get_job(second.id).status == JobStatus.SUCCEEDED
        assert max_active == 1

    asyncio.run(scenario())


def test_worker_can_cancel_before_run(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        job = make_job(repository)

        async def runner(_job):
            raise AssertionError("cancelled job should not run")

        queue = InProcessJobQueue(
            repository=repository,
            runner=runner,
            job_timeout_seconds=1,
        )

        await queue.cancel(job.id)
        await queue.run(job.id)

        updated = repository.get_job(job.id)
        assert updated.status == JobStatus.CANCELLED

    asyncio.run(scenario())
