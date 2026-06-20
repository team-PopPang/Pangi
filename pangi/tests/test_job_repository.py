from pangi.domain import JobStatus, JobType
from pangi.repository import SQLiteJobRepository


def make_repo(tmp_path):
    return SQLiteJobRepository(tmp_path / "pangi.sqlite3")


def test_repository_creates_and_reuses_thread(tmp_path):
    repository = make_repo(tmp_path)

    first = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    second = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")

    assert first.id == second.id
    assert first.thread_ts == "171.1"


def test_repository_creates_job_and_finds_by_event_id(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")

    job = repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        requester_user_id="U123",
        prompt="분석해줘",
    )
    found = repository.find_job_by_event_id("Ev123")

    assert found == job
    assert job.job_type == JobType.ANALYZE
    assert job.status == JobStatus.QUEUED
    assert job.prompt == "분석해줘"


def test_repository_updates_job_status(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    job = repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        requester_user_id="U123",
        prompt="분석해줘",
    )

    updated = repository.update_job_status(job.id, JobStatus.FAILED, error_message="boom")

    assert updated.status == JobStatus.FAILED
    assert updated.error_message == "boom"


def test_repository_updates_job_result(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    job = repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        requester_user_id="U123",
        prompt="분석해줘",
    )

    updated = repository.update_job_result(
        job.id,
        worktree_path="/tmp/pangi/worktrees/job_123",
        stdout="analysis",
        stderr="warning",
    )

    assert updated.worktree_path == "/tmp/pangi/worktrees/job_123"
    assert updated.stdout == "analysis"
    assert updated.stderr == "warning"


def test_repository_prevents_duplicate_event_id(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        requester_user_id="U123",
        prompt="분석해줘",
    )

    existing = repository.find_job_by_event_id("Ev123")

    assert existing is not None


def test_repository_appends_codex_run(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    job = repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        requester_user_id="U123",
        prompt="분석해줘",
    )

    run = repository.append_codex_run(
        job_id=job.id,
        mode="read-only",
        command='["codex", "exec"]',
        prompt="분석해줘",
        stdout="ok",
        exit_code=0,
    )

    assert run.job_id == job.id
    assert run.stdout == "ok"
    assert run.exit_code == 0
    assert run.timed_out is False


def test_repository_lists_recent_rows(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    job = repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        requester_user_id="U123",
        prompt="분석해줘",
    )
    run = repository.append_codex_run(
        job_id=job.id,
        mode="read-only",
        command='["codex", "exec"]',
        prompt="분석해줘",
        exit_code=0,
    )

    listed_thread = repository.list_threads(limit=10)[0]
    assert listed_thread.id == thread.id
    assert listed_thread.last_job_id == job.id
    assert repository.list_jobs(limit=10) == [job]
    assert repository.list_codex_runs(limit=10) == [run]


def test_repository_persists_jobs_across_instances(tmp_path):
    db_path = tmp_path / "pangi.sqlite3"
    first_repository = SQLiteJobRepository(db_path)
    thread = first_repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    job = first_repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        requester_user_id="U123",
        prompt="분석해줘",
    )

    second_repository = SQLiteJobRepository(db_path)
    found = second_repository.get_job(job.id)

    assert found == job
