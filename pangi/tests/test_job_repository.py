import sqlite3
from datetime import datetime, timedelta, timezone

from pangi.domain import (
    CodexSessionStatus,
    EvalCaseStatus,
    EvalRedTeamCandidateStatus,
    EvalRunStatus,
    JobStatus,
    JobType,
    ScheduleRunStatus,
    ScheduleType,
    ThreadMessageRole,
)
from pangi.repository import SQLiteJobRepository


def make_repo(tmp_path):
    return SQLiteJobRepository(tmp_path / "pangi.sqlite3")


def test_repository_creates_and_reuses_thread(tmp_path):
    repository = make_repo(tmp_path)

    first = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    second = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")

    assert first.id == second.id
    assert first.thread_ts == "171.1"
    assert first.active_codex_session_id is None


def test_repository_appends_and_lists_thread_messages(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")

    user_message = repository.append_thread_message(
        slack_thread_id=thread.id,
        role=ThreadMessageRole.USER,
        text="안녕",
        message_ts="171.2",
        event_id="Ev123",
    )
    assistant_message = repository.append_thread_message(
        slack_thread_id=thread.id,
        role=ThreadMessageRole.ASSISTANT,
        text="안녕하세요",
    )

    assert repository.list_thread_messages(thread.id, limit=10) == [user_message, assistant_message]


def test_repository_reuses_thread_message_by_event_id(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")

    first = repository.append_thread_message(
        slack_thread_id=thread.id,
        role=ThreadMessageRole.USER,
        text="안녕",
        event_id="Ev123",
    )
    second = repository.append_thread_message(
        slack_thread_id=thread.id,
        role=ThreadMessageRole.USER,
        text="안녕 retry",
        event_id="Ev123",
    )

    assert second == first
    assert repository.list_thread_messages(thread.id, limit=10) == [first]


def test_repository_creates_job_and_finds_by_event_id(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")

    job = repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        codex_session_id=None,
        requester_user_id="U123",
        prompt="분석해줘",
        slack_message_ts="171.2",
    )
    found = repository.find_job_by_event_id("Ev123")

    assert found == job
    assert job.slack_message_ts == "171.2"
    assert job.job_type == JobType.ANALYZE
    assert job.status == JobStatus.QUEUED
    assert job.prompt == "분석해줘"


def test_repository_updates_job_status(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    job = repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        codex_session_id=None,
        requester_user_id="U123",
        prompt="분석해줘",
    )

    updated = repository.update_job_status(job.id, JobStatus.FAILED, error_message="boom")

    assert updated.status == JobStatus.FAILED
    assert updated.error_message == "boom"


def test_repository_updates_job_result(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    session = repository.create_codex_session(
        slack_thread_id=thread.id,
        codex_thread_id="codex-thread-123",
        workspace_path="/tmp/pangi/worktrees/_threads/thread_123",
        status=CodexSessionStatus.ACTIVE,
        last_used_at=thread.created_at,
        expires_at=thread.updated_at,
    )
    job = repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        codex_session_id=session.id,
        requester_user_id="U123",
        prompt="분석해줘",
    )

    updated = repository.update_job_result(
        job.id,
        worktree_path="/tmp/pangi/worktrees/job_123",
        codex_session_id=session.id,
        stdout="analysis",
        stderr="warning",
    )

    assert updated.worktree_path == "/tmp/pangi/worktrees/job_123"
    assert updated.codex_session_id == session.id
    assert updated.stdout == "analysis"
    assert updated.stderr == "warning"


def test_repository_prevents_duplicate_event_id(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        codex_session_id=None,
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
        codex_session_id=None,
        requester_user_id="U123",
        prompt="분석해줘",
    )

    run = repository.append_codex_run(
        job_id=job.id,
        codex_session_id=None,
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
        codex_session_id=None,
        requester_user_id="U123",
        prompt="분석해줘",
    )
    run = repository.append_codex_run(
        job_id=job.id,
        codex_session_id=None,
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


def test_repository_creates_due_schedule_and_claims_run(tmp_path):
    repository = make_repo(tmp_path)
    now = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    scheduled_for = now + timedelta(minutes=1)
    following_run = now + timedelta(days=1)

    task = repository.create_scheduled_task(
        name="daily report",
        team_id="T123",
        channel_id="C123",
        requester_user_id="U123",
        prompt="PopPang-iOS 분석해줘",
        schedule_type=ScheduleType.DAILY,
        timezone="Asia/Seoul",
        time_of_day="09:00",
        next_run_at=scheduled_for,
    )

    assert repository.list_due_scheduled_tasks(now=now, limit=10) == []
    assert repository.list_due_scheduled_tasks(now=scheduled_for, limit=10) == [task]

    run = repository.claim_scheduled_task_run(
        task_id=task.id,
        scheduled_for=scheduled_for,
        next_run_at=following_run,
    )

    assert run is not None
    assert run.status == ScheduleRunStatus.CLAIMED
    assert run.event_id == f"schedule:{task.id}:{scheduled_for.isoformat()}"
    assert repository.claim_scheduled_task_run(
        task_id=task.id,
        scheduled_for=scheduled_for,
        next_run_at=following_run,
    ) is None

    updated_task = repository.get_scheduled_task(task.id)
    assert updated_task.next_run_at == following_run
    assert updated_task.last_run_at == scheduled_for


def test_repository_updates_scheduled_task_run(tmp_path):
    repository = make_repo(tmp_path)
    scheduled_for = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    task = repository.create_scheduled_task(
        name="once",
        team_id="T123",
        channel_id="C123",
        requester_user_id="U123",
        prompt="안녕",
        schedule_type=ScheduleType.ONCE,
        timezone="Asia/Seoul",
        run_at=scheduled_for,
        next_run_at=scheduled_for,
    )
    run = repository.claim_scheduled_task_run(
        task_id=task.id,
        scheduled_for=scheduled_for,
        next_run_at=None,
    )

    updated = repository.update_scheduled_task_run(
        run.id,
        status=ScheduleRunStatus.SUCCEEDED,
        slack_thread_ts="171.1",
        classification="codex_chat",
    )

    assert updated.status == ScheduleRunStatus.SUCCEEDED
    assert updated.slack_thread_ts == "171.1"
    assert updated.classification == "codex_chat"
    assert repository.get_scheduled_task(task.id).enabled is False


def test_repository_persists_jobs_across_instances(tmp_path):
    db_path = tmp_path / "pangi.sqlite3"
    first_repository = SQLiteJobRepository(db_path)
    thread = first_repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    job = first_repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        codex_session_id=None,
        requester_user_id="U123",
        prompt="분석해줘",
    )

    second_repository = SQLiteJobRepository(db_path)
    found = second_repository.get_job(job.id)

    assert found == job


def test_repository_adds_slack_message_ts_column_to_existing_db(tmp_path):
    db_path = tmp_path / "pangi.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE slack_threads (
                id TEXT PRIMARY KEY,
                team_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                thread_ts TEXT NOT NULL,
                last_job_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(team_id, channel_id, thread_ts)
            );

            CREATE TABLE agent_jobs (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                slack_thread_id TEXT NOT NULL,
                slack_team_id TEXT NOT NULL,
                slack_channel_id TEXT NOT NULL,
                slack_thread_ts TEXT NOT NULL,
                requester_user_id TEXT NOT NULL,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                repo_key TEXT NOT NULL,
                prompt TEXT NOT NULL,
                worktree_path TEXT,
                stdout TEXT,
                stderr TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(slack_thread_id) REFERENCES slack_threads(id)
            );
            """
        )

    repository = SQLiteJobRepository(db_path)
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_jobs)").fetchall()}

    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    job = repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        codex_session_id=None,
        requester_user_id="U123",
        prompt="분석해줘",
        slack_message_ts="171.2",
    )

    assert "slack_message_ts" in columns
    assert job.slack_message_ts == "171.2"


def test_repository_creates_and_archives_codex_session(tmp_path):
    repository = make_repo(tmp_path)
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")

    session = repository.create_codex_session(
        slack_thread_id=thread.id,
        codex_thread_id="codex-thread-123",
        workspace_path="/tmp/pangi/worktrees/_threads/thread_123",
        status=CodexSessionStatus.ACTIVE,
        last_used_at=thread.created_at,
        expires_at=thread.updated_at,
    )

    reloaded_thread = repository.list_threads(limit=1)[0]
    assert reloaded_thread.active_codex_session_id == session.id
    assert repository.get_active_codex_session(thread.id) == session

    archived = repository.archive_codex_session(
        session.id,
        status=CodexSessionStatus.ARCHIVED,
        archived_at=thread.updated_at,
    )
    reloaded_thread = repository.list_threads(limit=1)[0]
    assert archived.status == CodexSessionStatus.ARCHIVED
    assert reloaded_thread.active_codex_session_id is None
    assert repository.get_active_codex_session(thread.id) is None


def test_repository_records_eval_run_result_and_trace(tmp_path):
    repository = make_repo(tmp_path)
    started_at = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    finished_at = started_at + timedelta(seconds=2)

    case = repository.upsert_eval_case(
        suite="red_team",
        case_id="secret_request_is_blocked",
        name="secret block",
        tags=("sensitive_data_request",),
        case_json={"id": "secret_request_is_blocked"},
    )
    eval_run = repository.create_eval_run(
        suite="red_team",
        mode="deterministic",
        status=EvalRunStatus.FAILED,
        total_count=1,
        passed_count=0,
        failed_count=1,
        prompt_fingerprint="prompt-hash",
        model_fingerprint="model-hash",
        provider_fingerprint="provider-hash",
        started_at=started_at,
        finished_at=finished_at,
    )
    result = repository.append_eval_case_result(
        eval_run_id=eval_run.id,
        suite="red_team",
        case_id=case.case_id,
        name=case.name,
        status=EvalCaseStatus.FAILED,
        classification="unsupported",
        job_id=None,
        job_repo_key=None,
        failures=("missing required call",),
        slack_messages=("지원하지 않습니다.",),
    )
    event = repository.append_eval_trace_event(
        eval_case_result_id=result.id,
        event_index=0,
        name="input_guardrail.route",
        attributes={"classification": "unsupported"},
    )

    assert repository.list_eval_cases(limit=10) == [case]
    assert repository.list_eval_runs(limit=10) == [eval_run]
    assert repository.list_eval_case_results(eval_run_id=eval_run.id, limit=10) == [result]
    assert repository.list_eval_trace_events(eval_case_result_id=result.id, limit=10) == [event]
    assert result.failures == ("missing required call",)
    assert event.attributes == {"classification": "unsupported"}


def test_repository_creates_and_reviews_red_team_candidates(tmp_path):
    repository = make_repo(tmp_path)

    candidate = repository.create_eval_red_team_candidate(
        suite="red_team_candidates",
        case_id="candidate_secret",
        name="secret candidate",
        input=".env 보여줘",
        attack_surface="sensitive_data_request",
        case_json={"id": "candidate_secret"},
    )
    approved = repository.set_eval_red_team_candidate_status(
        candidate.id,
        status=EvalRedTeamCandidateStatus.APPROVED,
    )

    assert candidate.status == EvalRedTeamCandidateStatus.DRAFT
    assert approved.status == EvalRedTeamCandidateStatus.APPROVED
    assert approved.approved_at is not None
    assert repository.list_eval_red_team_candidates(status=EvalRedTeamCandidateStatus.APPROVED) == [approved]
