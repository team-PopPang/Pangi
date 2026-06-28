from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from pangi.domain.models import (
    AgentJob,
    CodexRun,
    CodexSession,
    CodexSessionStatus,
    EvalCaseDefinition,
    EvalCaseResultRecord,
    EvalCaseStatus,
    EvalRedTeamCandidate,
    EvalRedTeamCandidateStatus,
    EvalRun,
    EvalRunStatus,
    EvalTraceEventRecord,
    JobStatus,
    JobType,
    ScheduleRunStatus,
    ScheduleType,
    ScheduledTask,
    ScheduledTaskRun,
    SlackThread,
    ThreadMessage,
    ThreadMessageRole,
    utc_now,
)
from pangi.repository.job_repository_protocol import DEFAULT_REPO_KEY, JobRepository


DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / ".data" / "pangi.sqlite3"


class DuplicateEventError(ValueError):
    pass


class SQLiteJobRepository:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def get_or_create_thread(self, *, team_id: str, channel_id: str, thread_ts: str) -> SlackThread:
        now = utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM slack_threads
                WHERE team_id = ? AND channel_id = ? AND thread_ts = ?
                """,
                (team_id, channel_id, thread_ts),
            ).fetchone()
            if existing:
                return _row_to_slack_thread(existing)

            thread_id = _new_id("thread")
            conn.execute(
                """
                INSERT INTO slack_threads (
                    id, team_id, channel_id, thread_ts, last_job_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, ?, ?)
                """,
                (thread_id, team_id, channel_id, thread_ts, _dump_dt(now), _dump_dt(now)),
            )
            return self._get_thread(conn, thread_id)

    def append_thread_message(
        self,
        *,
        slack_thread_id: str,
        role: ThreadMessageRole,
        text: str,
        message_ts: str | None = None,
        event_id: str | None = None,
        source_job_id: str | None = None,
    ) -> ThreadMessage:
        now = utc_now()
        message_id = _new_id("msg")
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO thread_messages (
                        id, slack_thread_id, role, text, message_ts, event_id, source_job_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        slack_thread_id,
                        role.value,
                        text,
                        message_ts,
                        event_id,
                        source_job_id,
                        _dump_dt(now),
                    ),
                )
                conn.execute(
                    "UPDATE slack_threads SET updated_at = ? WHERE id = ?",
                    (_dump_dt(now), slack_thread_id),
                )
                return self._get_thread_message(conn, message_id)
        except sqlite3.IntegrityError as error:
            if event_id and "thread_messages.event_id" in str(error):
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT * FROM thread_messages WHERE event_id = ?",
                        (event_id,),
                    ).fetchone()
                    if row is not None:
                        return _row_to_thread_message(row)
            raise

    def list_thread_messages(self, slack_thread_id: str, *, limit: int = 20) -> list[ThreadMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM thread_messages
                WHERE slack_thread_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (slack_thread_id, _normalize_limit(limit)),
            ).fetchall()
            return [_row_to_thread_message(row) for row in reversed(rows)]

    def create_job(
        self,
        *,
        event_id: str,
        slack_thread: SlackThread,
        codex_session_id: str | None,
        requester_user_id: str,
        prompt: str,
        slack_message_ts: str | None = None,
        job_type: JobType = JobType.ANALYZE,
        repo_key: str = DEFAULT_REPO_KEY,
    ) -> AgentJob:
        now = utc_now()
        job_id = _new_id("job")
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO agent_jobs (
                        id, event_id, slack_thread_id, codex_session_id, slack_team_id, slack_channel_id,
                        slack_thread_ts, slack_message_ts, requester_user_id, job_type, status, repo_key,
                        prompt, worktree_path, stdout, stderr, error_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        job_id,
                        event_id,
                        slack_thread.id,
                        codex_session_id,
                        slack_thread.team_id,
                        slack_thread.channel_id,
                        slack_thread.thread_ts,
                        slack_message_ts,
                        requester_user_id,
                        job_type.value,
                        JobStatus.QUEUED.value,
                        repo_key,
                        prompt,
                        _dump_dt(now),
                        _dump_dt(now),
                    ),
                )
                conn.execute(
                    "UPDATE slack_threads SET last_job_id = ?, updated_at = ? WHERE id = ?",
                    (job_id, _dump_dt(now), slack_thread.id),
                )
                return self._get_job(conn, job_id)
        except sqlite3.IntegrityError as error:
            if "agent_jobs.event_id" in str(error):
                raise DuplicateEventError(event_id) from error
            raise

    def get_job(self, job_id: str) -> AgentJob | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM agent_jobs WHERE id = ?", (job_id,)).fetchone()
            return _row_to_agent_job(row) if row else None

    def find_job_by_event_id(self, event_id: str) -> AgentJob | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM agent_jobs WHERE event_id = ?", (event_id,)).fetchone()
            return _row_to_agent_job(row) if row else None

    def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error_message: str | None = None,
    ) -> AgentJob:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_jobs
                SET status = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, error_message, _dump_dt(now), job_id),
            )
            job = self._get_job(conn, job_id)
            return job

    def update_job_result(
        self,
        job_id: str,
        *,
        worktree_path: str | None = None,
        codex_session_id: str | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
        error_message: str | None = None,
    ) -> AgentJob:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_jobs
                SET worktree_path = COALESCE(?, worktree_path),
                    codex_session_id = COALESCE(?, codex_session_id),
                    stdout = COALESCE(?, stdout),
                    stderr = COALESCE(?, stderr),
                    error_message = COALESCE(?, error_message),
                    updated_at = ?
                WHERE id = ?
                """,
                (worktree_path, codex_session_id, stdout, stderr, error_message, _dump_dt(now), job_id),
            )
            job = self._get_job(conn, job_id)
            return job

    def append_codex_run(
        self,
        *,
        job_id: str,
        codex_session_id: str | None,
        mode: str,
        command: str,
        prompt: str,
        stdout: str | None = None,
        stderr: str | None = None,
        exit_code: int | None = None,
        timed_out: bool = False,
        workspace_path: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> CodexRun:
        now = utc_now()
        started_at = started_at or now
        finished_at = finished_at or now
        run_id = _new_id("run")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_runs (
                    id, job_id, codex_session_id, mode, command, prompt, stdout, stderr, exit_code,
                    timed_out, workspace_path, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    job_id,
                    codex_session_id,
                    mode,
                    command,
                    prompt,
                    stdout,
                    stderr,
                    exit_code,
                    1 if timed_out else 0,
                    workspace_path,
                    _dump_dt(started_at),
                    _dump_dt(finished_at),
                ),
            )
            row = conn.execute("SELECT * FROM codex_runs WHERE id = ?", (run_id,)).fetchone()
            return _row_to_codex_run(row)

    def list_threads(self, *, limit: int = 50) -> list[SlackThread]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM slack_threads
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (_normalize_limit(limit),),
            ).fetchall()
            return [_row_to_slack_thread(row) for row in rows]

    def list_jobs(self, *, limit: int = 50) -> list[AgentJob]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_jobs
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (_normalize_limit(limit),),
            ).fetchall()
            return [_row_to_agent_job(row) for row in rows]

    def list_codex_runs(self, *, limit: int = 50) -> list[CodexRun]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM codex_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (_normalize_limit(limit),),
            ).fetchall()
            return [_row_to_codex_run(row) for row in rows]

    def create_scheduled_task(
        self,
        *,
        name: str,
        team_id: str,
        channel_id: str,
        requester_user_id: str,
        prompt: str,
        schedule_type: ScheduleType,
        timezone: str,
        next_run_at: datetime | None,
        time_of_day: str | None = None,
        days_of_week: str | None = None,
        run_at: datetime | None = None,
        enabled: bool = True,
    ) -> ScheduledTask:
        now = utc_now()
        task_id = _new_id("schedule")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scheduled_tasks (
                    id, name, enabled, team_id, channel_id, requester_user_id, prompt,
                    schedule_type, timezone, time_of_day, days_of_week, run_at,
                    next_run_at, last_run_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    task_id,
                    name,
                    1 if enabled else 0,
                    team_id,
                    channel_id,
                    requester_user_id,
                    prompt,
                    schedule_type.value,
                    timezone,
                    time_of_day,
                    days_of_week,
                    _dump_dt(run_at) if run_at else None,
                    _dump_dt(next_run_at) if next_run_at else None,
                    _dump_dt(now),
                    _dump_dt(now),
                ),
            )
            return self._get_scheduled_task(conn, task_id)

    def set_scheduled_task_enabled(self, task_id: str, *, enabled: bool) -> ScheduledTask:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if enabled else 0, _dump_dt(now), task_id),
            )
            return self._get_scheduled_task(conn, task_id)

    def get_scheduled_task(self, task_id: str) -> ScheduledTask | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
            return _row_to_scheduled_task(row) if row else None

    def list_scheduled_tasks(self, *, limit: int = 50) -> list[ScheduledTask]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_tasks
                ORDER BY enabled DESC, next_run_at ASC, updated_at DESC
                LIMIT ?
                """,
                (_normalize_limit(limit),),
            ).fetchall()
            return [_row_to_scheduled_task(row) for row in rows]

    def list_due_scheduled_tasks(self, *, now: datetime, limit: int = 20) -> list[ScheduledTask]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_tasks
                WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
                ORDER BY next_run_at ASC
                LIMIT ?
                """,
                (_dump_dt(now), _normalize_limit(limit)),
            ).fetchall()
            return [_row_to_scheduled_task(row) for row in rows]

    def claim_scheduled_task_run(
        self,
        *,
        task_id: str,
        scheduled_for: datetime,
        next_run_at: datetime | None,
    ) -> ScheduledTaskRun | None:
        now = utc_now()
        run_id = _new_id("schedule_run")
        event_id = f"schedule:{task_id}:{_dump_dt(scheduled_for)}"
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO scheduled_task_runs (
                        id, scheduled_task_id, scheduled_for, status, event_id, slack_thread_ts,
                        job_id, classification, error_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        run_id,
                        task_id,
                        _dump_dt(scheduled_for),
                        ScheduleRunStatus.CLAIMED.value,
                        event_id,
                        _dump_dt(now),
                        _dump_dt(now),
                    ),
                )
                conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET next_run_at = ?, last_run_at = ?, enabled = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        _dump_dt(next_run_at) if next_run_at else None,
                        _dump_dt(scheduled_for),
                        1 if next_run_at is not None else 0,
                        _dump_dt(now),
                        task_id,
                    ),
                )
                return self._get_scheduled_task_run(conn, run_id)
        except sqlite3.IntegrityError as error:
            if "scheduled_task_runs" in str(error):
                return None
            raise

    def update_scheduled_task_run(
        self,
        run_id: str,
        *,
        status: ScheduleRunStatus,
        slack_thread_ts: str | None = None,
        job_id: str | None = None,
        classification: str | None = None,
        error_message: str | None = None,
    ) -> ScheduledTaskRun:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE scheduled_task_runs
                SET status = ?,
                    slack_thread_ts = COALESCE(?, slack_thread_ts),
                    job_id = COALESCE(?, job_id),
                    classification = COALESCE(?, classification),
                    error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    slack_thread_ts,
                    job_id,
                    classification,
                    error_message,
                    _dump_dt(now),
                    run_id,
                ),
            )
            return self._get_scheduled_task_run(conn, run_id)

    def list_scheduled_task_runs(self, *, limit: int = 50) -> list[ScheduledTaskRun]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_task_runs
                ORDER BY scheduled_for DESC, updated_at DESC
                LIMIT ?
                """,
                (_normalize_limit(limit),),
            ).fetchall()
            return [_row_to_scheduled_task_run(row) for row in rows]

    def upsert_eval_case(
        self,
        *,
        suite: str,
        case_id: str,
        name: str,
        tags: tuple[str, ...],
        case_json: dict[str, object],
    ) -> EvalCaseDefinition:
        now = utc_now()
        row_id = _new_id("eval_case")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO eval_cases (
                    id, suite, case_id, name, tags, case_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(suite, case_id) DO UPDATE SET
                    name = excluded.name,
                    tags = excluded.tags,
                    case_json = excluded.case_json,
                    updated_at = excluded.updated_at
                """,
                (
                    row_id,
                    suite,
                    case_id,
                    name,
                    _dump_json(list(tags)),
                    _dump_json(case_json),
                    _dump_dt(now),
                    _dump_dt(now),
                ),
            )
            row = conn.execute(
                "SELECT * FROM eval_cases WHERE suite = ? AND case_id = ?",
                (suite, case_id),
            ).fetchone()
            return _row_to_eval_case_definition(row)

    def list_eval_cases(self, *, limit: int = 100) -> list[EvalCaseDefinition]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM eval_cases
                ORDER BY suite ASC, case_id ASC
                LIMIT ?
                """,
                (_normalize_limit(limit),),
            ).fetchall()
            return [_row_to_eval_case_definition(row) for row in rows]

    def create_eval_run(
        self,
        *,
        suite: str,
        mode: str,
        status: EvalRunStatus,
        total_count: int,
        passed_count: int,
        failed_count: int,
        prompt_fingerprint: str | None,
        model_fingerprint: str | None,
        provider_fingerprint: str | None,
        started_at: datetime,
        finished_at: datetime,
    ) -> EvalRun:
        now = utc_now()
        run_id = _new_id("eval_run")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO eval_runs (
                    id, suite, mode, status, total_count, passed_count, failed_count,
                    prompt_fingerprint, model_fingerprint, provider_fingerprint,
                    started_at, finished_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    suite,
                    mode,
                    status.value,
                    total_count,
                    passed_count,
                    failed_count,
                    prompt_fingerprint,
                    model_fingerprint,
                    provider_fingerprint,
                    _dump_dt(started_at),
                    _dump_dt(finished_at),
                    _dump_dt(now),
                    _dump_dt(now),
                ),
            )
            return self._get_eval_run(conn, run_id)

    def append_eval_case_result(
        self,
        *,
        eval_run_id: str,
        suite: str,
        case_id: str,
        name: str,
        status: EvalCaseStatus,
        classification: str,
        job_id: str | None,
        job_repo_key: str | None,
        failures: tuple[str, ...],
        slack_messages: tuple[str, ...],
    ) -> EvalCaseResultRecord:
        now = utc_now()
        result_id = _new_id("eval_result")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO eval_case_results (
                    id, eval_run_id, suite, case_id, name, status, classification,
                    job_id, job_repo_key, failures, slack_messages, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    eval_run_id,
                    suite,
                    case_id,
                    name,
                    status.value,
                    classification,
                    job_id,
                    job_repo_key,
                    _dump_json(list(failures)),
                    _dump_json(list(slack_messages)),
                    _dump_dt(now),
                ),
            )
            return self._get_eval_case_result(conn, result_id)

    def append_eval_trace_event(
        self,
        *,
        eval_case_result_id: str,
        event_index: int,
        name: str,
        attributes: dict[str, object],
    ) -> EvalTraceEventRecord:
        now = utc_now()
        event_id = _new_id("eval_trace")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO eval_trace_events (
                    id, eval_case_result_id, event_index, name, attributes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    eval_case_result_id,
                    event_index,
                    name,
                    _dump_json(attributes),
                    _dump_dt(now),
                ),
            )
            return self._get_eval_trace_event(conn, event_id)

    def list_eval_runs(self, *, limit: int = 50) -> list[EvalRun]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM eval_runs
                ORDER BY started_at DESC, created_at DESC
                LIMIT ?
                """,
                (_normalize_limit(limit),),
            ).fetchall()
            return [_row_to_eval_run(row) for row in rows]

    def list_eval_case_results(
        self,
        *,
        eval_run_id: str | None = None,
        limit: int = 100,
    ) -> list[EvalCaseResultRecord]:
        with self._connect() as conn:
            if eval_run_id:
                rows = conn.execute(
                    """
                    SELECT * FROM eval_case_results
                    WHERE eval_run_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (eval_run_id, _normalize_limit(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM eval_case_results
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (_normalize_limit(limit),),
                ).fetchall()
            return [_row_to_eval_case_result(row) for row in rows]

    def list_eval_trace_events(
        self,
        *,
        eval_case_result_id: str | None = None,
        limit: int = 200,
    ) -> list[EvalTraceEventRecord]:
        with self._connect() as conn:
            if eval_case_result_id:
                rows = conn.execute(
                    """
                    SELECT * FROM eval_trace_events
                    WHERE eval_case_result_id = ?
                    ORDER BY event_index ASC
                    LIMIT ?
                    """,
                    (eval_case_result_id, _normalize_limit(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM eval_trace_events
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (_normalize_limit(limit),),
                ).fetchall()
            return [_row_to_eval_trace_event(row) for row in rows]

    def create_eval_red_team_candidate(
        self,
        *,
        suite: str,
        case_id: str,
        name: str,
        input: str,
        attack_surface: str,
        case_json: dict[str, object],
    ) -> EvalRedTeamCandidate:
        now = utc_now()
        candidate_id = _new_id("eval_candidate")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO eval_red_team_candidates (
                    id, suite, case_id, name, input, attack_surface, status,
                    case_json, created_at, updated_at, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(case_id) DO UPDATE SET
                    suite = excluded.suite,
                    name = excluded.name,
                    input = excluded.input,
                    attack_surface = excluded.attack_surface,
                    case_json = excluded.case_json,
                    updated_at = excluded.updated_at
                """,
                (
                    candidate_id,
                    suite,
                    case_id,
                    name,
                    input,
                    attack_surface,
                    EvalRedTeamCandidateStatus.DRAFT.value,
                    _dump_json(case_json),
                    _dump_dt(now),
                    _dump_dt(now),
                ),
            )
            row = conn.execute(
                "SELECT * FROM eval_red_team_candidates WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            return _row_to_eval_red_team_candidate(row)

    def set_eval_red_team_candidate_status(
        self,
        candidate_id: str,
        *,
        status: EvalRedTeamCandidateStatus,
    ) -> EvalRedTeamCandidate:
        now = utc_now()
        approved_at = now if status == EvalRedTeamCandidateStatus.APPROVED else None
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE eval_red_team_candidates
                SET status = ?, approved_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    _dump_dt(approved_at) if approved_at else None,
                    _dump_dt(now),
                    candidate_id,
                ),
            )
            return self._get_eval_red_team_candidate(conn, candidate_id)

    def list_eval_red_team_candidates(
        self,
        *,
        status: EvalRedTeamCandidateStatus | None = None,
        limit: int = 50,
    ) -> list[EvalRedTeamCandidate]:
        with self._connect() as conn:
            if status is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM eval_red_team_candidates
                    WHERE status = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (status.value, _normalize_limit(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM eval_red_team_candidates
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (_normalize_limit(limit),),
                ).fetchall()
            return [_row_to_eval_red_team_candidate(row) for row in rows]

    def get_active_codex_session(self, slack_thread_id: str) -> CodexSession | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT s.*
                FROM codex_sessions s
                JOIN slack_threads t ON t.active_codex_session_id = s.id
                WHERE t.id = ?
                """,
                (slack_thread_id,),
            ).fetchone()
            return _row_to_codex_session(row) if row else None

    def create_codex_session(
        self,
        *,
        slack_thread_id: str,
        codex_thread_id: str,
        workspace_path: str,
        status: CodexSessionStatus,
        last_used_at: datetime,
        expires_at: datetime,
    ) -> CodexSession:
        now = utc_now()
        session_id = _new_id("session")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_sessions (
                    id, slack_thread_id, codex_thread_id, workspace_path, status,
                    last_used_at, expires_at, archived_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    session_id,
                    slack_thread_id,
                    codex_thread_id,
                    workspace_path,
                    status.value,
                    _dump_dt(last_used_at),
                    _dump_dt(expires_at),
                    _dump_dt(now),
                    _dump_dt(now),
                ),
            )
            conn.execute(
                "UPDATE slack_threads SET active_codex_session_id = ?, updated_at = ? WHERE id = ?",
                (session_id, _dump_dt(now), slack_thread_id),
            )
            return self._get_codex_session(conn, session_id)

    def update_codex_session_activity(
        self,
        codex_session_id: str,
        *,
        status: CodexSessionStatus | None = None,
        last_used_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> CodexSession:
        now = utc_now()
        with self._connect() as conn:
            existing = self._get_codex_session(conn, codex_session_id)
            conn.execute(
                """
                UPDATE codex_sessions
                SET status = ?,
                    last_used_at = ?,
                    expires_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    (status or existing.status).value,
                    _dump_dt(last_used_at or existing.last_used_at),
                    _dump_dt(expires_at or existing.expires_at),
                    _dump_dt(now),
                    codex_session_id,
                ),
            )
            return self._get_codex_session(conn, codex_session_id)

    def archive_codex_session(
        self,
        codex_session_id: str,
        *,
        status: CodexSessionStatus,
        archived_at: datetime | None,
    ) -> CodexSession:
        now = utc_now()
        with self._connect() as conn:
            session = self._get_codex_session(conn, codex_session_id)
            conn.execute(
                """
                UPDATE codex_sessions
                SET status = ?, archived_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    _dump_dt(archived_at or now),
                    _dump_dt(now),
                    codex_session_id,
                ),
            )
            conn.execute(
                """
                UPDATE slack_threads
                SET active_codex_session_id = NULL, updated_at = ?
                WHERE id = ? AND active_codex_session_id = ?
                """,
                (_dump_dt(now), session.slack_thread_id, codex_session_id),
            )
            return self._get_codex_session(conn, codex_session_id)

    def list_expired_active_codex_sessions(self, *, now: datetime, limit: int = 100) -> list[CodexSession]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*
                FROM codex_sessions s
                JOIN slack_threads t ON t.active_codex_session_id = s.id
                WHERE s.status = ? AND s.expires_at <= ?
                ORDER BY s.expires_at ASC
                LIMIT ?
                """,
                (
                    CodexSessionStatus.ACTIVE.value,
                    _dump_dt(now),
                    _normalize_limit(limit),
                ),
            ).fetchall()
            return [_row_to_codex_session(row) for row in rows]

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS slack_threads (
                    id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    last_job_id TEXT,
                    active_codex_session_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(team_id, channel_id, thread_ts)
                );

                CREATE TABLE IF NOT EXISTS agent_jobs (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    slack_thread_id TEXT NOT NULL,
                    codex_session_id TEXT,
                    slack_team_id TEXT NOT NULL,
                    slack_channel_id TEXT NOT NULL,
                    slack_thread_ts TEXT NOT NULL,
                    slack_message_ts TEXT,
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
                    FOREIGN KEY(slack_thread_id) REFERENCES slack_threads(id),
                    FOREIGN KEY(codex_session_id) REFERENCES codex_sessions(id)
                );

                CREATE TABLE IF NOT EXISTS codex_sessions (
                    id TEXT PRIMARY KEY,
                    slack_thread_id TEXT NOT NULL,
                    codex_thread_id TEXT NOT NULL UNIQUE,
                    workspace_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    archived_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(slack_thread_id) REFERENCES slack_threads(id)
                );

                CREATE TABLE IF NOT EXISTS thread_messages (
                    id TEXT PRIMARY KEY,
                    slack_thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    message_ts TEXT,
                    event_id TEXT UNIQUE,
                    source_job_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(slack_thread_id) REFERENCES slack_threads(id),
                    FOREIGN KEY(source_job_id) REFERENCES agent_jobs(id)
                );

                CREATE TABLE IF NOT EXISTS codex_runs (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    codex_session_id TEXT,
                    mode TEXT NOT NULL,
                    command TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    stdout TEXT,
                    stderr TEXT,
                    exit_code INTEGER,
                    timed_out INTEGER NOT NULL,
                    workspace_path TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(job_id) REFERENCES agent_jobs(id),
                    FOREIGN KEY(codex_session_id) REFERENCES codex_sessions(id)
                );

                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    requester_user_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    time_of_day TEXT,
                    days_of_week TEXT,
                    run_at TEXT,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scheduled_task_runs (
                    id TEXT PRIMARY KEY,
                    scheduled_task_id TEXT NOT NULL,
                    scheduled_for TEXT NOT NULL,
                    status TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    slack_thread_ts TEXT,
                    job_id TEXT,
                    classification TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scheduled_task_id, scheduled_for),
                    FOREIGN KEY(scheduled_task_id) REFERENCES scheduled_tasks(id),
                    FOREIGN KEY(job_id) REFERENCES agent_jobs(id)
                );

                CREATE TABLE IF NOT EXISTS eval_cases (
                    id TEXT PRIMARY KEY,
                    suite TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    case_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(suite, case_id)
                );

                CREATE TABLE IF NOT EXISTS eval_runs (
                    id TEXT PRIMARY KEY,
                    suite TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_count INTEGER NOT NULL,
                    passed_count INTEGER NOT NULL,
                    failed_count INTEGER NOT NULL,
                    prompt_fingerprint TEXT,
                    model_fingerprint TEXT,
                    provider_fingerprint TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS eval_case_results (
                    id TEXT PRIMARY KEY,
                    eval_run_id TEXT NOT NULL,
                    suite TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    job_id TEXT,
                    job_repo_key TEXT,
                    failures TEXT NOT NULL,
                    slack_messages TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(eval_run_id) REFERENCES eval_runs(id)
                );

                CREATE TABLE IF NOT EXISTS eval_trace_events (
                    id TEXT PRIMARY KEY,
                    eval_case_result_id TEXT NOT NULL,
                    event_index INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    attributes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(eval_case_result_id, event_index),
                    FOREIGN KEY(eval_case_result_id) REFERENCES eval_case_results(id)
                );

                CREATE TABLE IF NOT EXISTS eval_red_team_candidates (
                    id TEXT PRIMARY KEY,
                    suite TEXT NOT NULL,
                    case_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    input TEXT NOT NULL,
                    attack_surface TEXT NOT NULL,
                    status TEXT NOT NULL,
                    case_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT
                );
                """
            )
            self._ensure_slack_threads_active_session_id(conn)
            self._ensure_agent_jobs_codex_session_id(conn)
            self._ensure_agent_jobs_message_ts(conn)
            self._ensure_codex_runs_codex_session_id(conn)
            self._ensure_codex_runs_workspace_path(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _get_thread(self, conn: sqlite3.Connection, thread_id: str) -> SlackThread:
        row = conn.execute("SELECT * FROM slack_threads WHERE id = ?", (thread_id,)).fetchone()
        if row is None:
            raise KeyError(thread_id)
        return _row_to_slack_thread(row)

    def _get_thread_message(self, conn: sqlite3.Connection, message_id: str) -> ThreadMessage:
        row = conn.execute("SELECT * FROM thread_messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise KeyError(message_id)
        return _row_to_thread_message(row)

    def _get_job(self, conn: sqlite3.Connection, job_id: str) -> AgentJob:
        row = conn.execute("SELECT * FROM agent_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _row_to_agent_job(row)

    def _get_codex_session(self, conn: sqlite3.Connection, codex_session_id: str) -> CodexSession:
        row = conn.execute("SELECT * FROM codex_sessions WHERE id = ?", (codex_session_id,)).fetchone()
        if row is None:
            raise KeyError(codex_session_id)
        return _row_to_codex_session(row)

    def _get_scheduled_task(self, conn: sqlite3.Connection, task_id: str) -> ScheduledTask:
        row = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return _row_to_scheduled_task(row)

    def _get_scheduled_task_run(self, conn: sqlite3.Connection, run_id: str) -> ScheduledTaskRun:
        row = conn.execute("SELECT * FROM scheduled_task_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _row_to_scheduled_task_run(row)

    def _get_eval_run(self, conn: sqlite3.Connection, run_id: str) -> EvalRun:
        row = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _row_to_eval_run(row)

    def _get_eval_case_result(self, conn: sqlite3.Connection, result_id: str) -> EvalCaseResultRecord:
        row = conn.execute("SELECT * FROM eval_case_results WHERE id = ?", (result_id,)).fetchone()
        if row is None:
            raise KeyError(result_id)
        return _row_to_eval_case_result(row)

    def _get_eval_trace_event(self, conn: sqlite3.Connection, event_id: str) -> EvalTraceEventRecord:
        row = conn.execute("SELECT * FROM eval_trace_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        return _row_to_eval_trace_event(row)

    def _get_eval_red_team_candidate(self, conn: sqlite3.Connection, candidate_id: str) -> EvalRedTeamCandidate:
        row = conn.execute("SELECT * FROM eval_red_team_candidates WHERE id = ?", (candidate_id,)).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return _row_to_eval_red_team_candidate(row)

    def _ensure_slack_threads_active_session_id(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(slack_threads)").fetchall()
        }
        if "active_codex_session_id" not in columns:
            conn.execute("ALTER TABLE slack_threads ADD COLUMN active_codex_session_id TEXT")

    def _ensure_agent_jobs_message_ts(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(agent_jobs)").fetchall()
        }
        if "slack_message_ts" not in columns:
            conn.execute("ALTER TABLE agent_jobs ADD COLUMN slack_message_ts TEXT")

    def _ensure_agent_jobs_codex_session_id(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(agent_jobs)").fetchall()
        }
        if "codex_session_id" not in columns:
            conn.execute("ALTER TABLE agent_jobs ADD COLUMN codex_session_id TEXT")

    def _ensure_codex_runs_codex_session_id(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(codex_runs)").fetchall()
        }
        if "codex_session_id" not in columns:
            conn.execute("ALTER TABLE codex_runs ADD COLUMN codex_session_id TEXT")

    def _ensure_codex_runs_workspace_path(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(codex_runs)").fetchall()
        }
        if "workspace_path" not in columns:
            conn.execute("ALTER TABLE codex_runs ADD COLUMN workspace_path TEXT")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _normalize_limit(limit: int) -> int:
    return min(max(limit, 1), 200)


def _dump_dt(value: object) -> str:
    if not hasattr(value, "isoformat"):
        raise TypeError("Expected datetime-like value")
    return value.isoformat()


def _dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_json(value: str) -> object:
    return json.loads(value)


def _load_json_dict(value: str) -> dict[str, object]:
    data = _load_json(value)
    return data if isinstance(data, dict) else {}


def _load_json_tuple(value: str) -> tuple[str, ...]:
    data = _load_json(value)
    if not isinstance(data, list):
        return ()
    return tuple(str(item) for item in data)


def _load_dt(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)


def _row_to_slack_thread(row: sqlite3.Row) -> SlackThread:
    return SlackThread(
        id=row["id"],
        team_id=row["team_id"],
        channel_id=row["channel_id"],
        thread_ts=row["thread_ts"],
        last_job_id=row["last_job_id"],
        active_codex_session_id=row["active_codex_session_id"],
        created_at=_load_dt(row["created_at"]),
        updated_at=_load_dt(row["updated_at"]),
    )


def _row_to_thread_message(row: sqlite3.Row) -> ThreadMessage:
    return ThreadMessage(
        id=row["id"],
        slack_thread_id=row["slack_thread_id"],
        role=ThreadMessageRole(row["role"]),
        text=row["text"],
        message_ts=row["message_ts"],
        event_id=row["event_id"],
        source_job_id=row["source_job_id"],
        created_at=_load_dt(row["created_at"]),
    )


def _row_to_agent_job(row: sqlite3.Row) -> AgentJob:
    return AgentJob(
        id=row["id"],
        event_id=row["event_id"],
        slack_thread_id=row["slack_thread_id"],
        codex_session_id=row["codex_session_id"],
        slack_team_id=row["slack_team_id"],
        slack_channel_id=row["slack_channel_id"],
        slack_thread_ts=row["slack_thread_ts"],
        slack_message_ts=row["slack_message_ts"],
        requester_user_id=row["requester_user_id"],
        job_type=JobType(row["job_type"]),
        status=JobStatus(row["status"]),
        repo_key=row["repo_key"],
        prompt=row["prompt"],
        worktree_path=row["worktree_path"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        error_message=row["error_message"],
        created_at=_load_dt(row["created_at"]),
        updated_at=_load_dt(row["updated_at"]),
    )


def _row_to_codex_run(row: sqlite3.Row) -> CodexRun:
    return CodexRun(
        id=row["id"],
        job_id=row["job_id"],
        codex_session_id=row["codex_session_id"],
        mode=row["mode"],
        command=row["command"],
        prompt=row["prompt"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        exit_code=row["exit_code"],
        timed_out=bool(row["timed_out"]),
        workspace_path=row["workspace_path"],
        started_at=_load_dt(row["started_at"]),
        finished_at=_load_dt(row["finished_at"]) if row["finished_at"] else None,
    )


def _row_to_codex_session(row: sqlite3.Row) -> CodexSession:
    return CodexSession(
        id=row["id"],
        slack_thread_id=row["slack_thread_id"],
        codex_thread_id=row["codex_thread_id"],
        workspace_path=row["workspace_path"],
        status=CodexSessionStatus(row["status"]),
        last_used_at=_load_dt(row["last_used_at"]),
        expires_at=_load_dt(row["expires_at"]),
        archived_at=_load_dt(row["archived_at"]) if row["archived_at"] else None,
        created_at=_load_dt(row["created_at"]),
        updated_at=_load_dt(row["updated_at"]),
    )


def _row_to_scheduled_task(row: sqlite3.Row) -> ScheduledTask:
    return ScheduledTask(
        id=row["id"],
        name=row["name"],
        enabled=bool(row["enabled"]),
        team_id=row["team_id"],
        channel_id=row["channel_id"],
        requester_user_id=row["requester_user_id"],
        prompt=row["prompt"],
        schedule_type=ScheduleType(row["schedule_type"]),
        timezone=row["timezone"],
        time_of_day=row["time_of_day"],
        days_of_week=row["days_of_week"],
        run_at=_load_dt(row["run_at"]) if row["run_at"] else None,
        next_run_at=_load_dt(row["next_run_at"]) if row["next_run_at"] else None,
        last_run_at=_load_dt(row["last_run_at"]) if row["last_run_at"] else None,
        created_at=_load_dt(row["created_at"]),
        updated_at=_load_dt(row["updated_at"]),
    )


def _row_to_scheduled_task_run(row: sqlite3.Row) -> ScheduledTaskRun:
    return ScheduledTaskRun(
        id=row["id"],
        scheduled_task_id=row["scheduled_task_id"],
        scheduled_for=_load_dt(row["scheduled_for"]),
        status=ScheduleRunStatus(row["status"]),
        event_id=row["event_id"],
        slack_thread_ts=row["slack_thread_ts"],
        job_id=row["job_id"],
        classification=row["classification"],
        error_message=row["error_message"],
        created_at=_load_dt(row["created_at"]),
        updated_at=_load_dt(row["updated_at"]),
    )


def _row_to_eval_case_definition(row: sqlite3.Row) -> EvalCaseDefinition:
    return EvalCaseDefinition(
        id=row["id"],
        suite=row["suite"],
        case_id=row["case_id"],
        name=row["name"],
        tags=_load_json_tuple(row["tags"]),
        case_json=_load_json_dict(row["case_json"]),
        created_at=_load_dt(row["created_at"]),
        updated_at=_load_dt(row["updated_at"]),
    )


def _row_to_eval_run(row: sqlite3.Row) -> EvalRun:
    return EvalRun(
        id=row["id"],
        suite=row["suite"],
        mode=row["mode"],
        status=EvalRunStatus(row["status"]),
        total_count=row["total_count"],
        passed_count=row["passed_count"],
        failed_count=row["failed_count"],
        prompt_fingerprint=row["prompt_fingerprint"],
        model_fingerprint=row["model_fingerprint"],
        provider_fingerprint=row["provider_fingerprint"],
        started_at=_load_dt(row["started_at"]),
        finished_at=_load_dt(row["finished_at"]),
        created_at=_load_dt(row["created_at"]),
        updated_at=_load_dt(row["updated_at"]),
    )


def _row_to_eval_case_result(row: sqlite3.Row) -> EvalCaseResultRecord:
    return EvalCaseResultRecord(
        id=row["id"],
        eval_run_id=row["eval_run_id"],
        suite=row["suite"],
        case_id=row["case_id"],
        name=row["name"],
        status=EvalCaseStatus(row["status"]),
        classification=row["classification"],
        job_id=row["job_id"],
        job_repo_key=row["job_repo_key"],
        failures=_load_json_tuple(row["failures"]),
        slack_messages=_load_json_tuple(row["slack_messages"]),
        created_at=_load_dt(row["created_at"]),
    )


def _row_to_eval_trace_event(row: sqlite3.Row) -> EvalTraceEventRecord:
    return EvalTraceEventRecord(
        id=row["id"],
        eval_case_result_id=row["eval_case_result_id"],
        event_index=row["event_index"],
        name=row["name"],
        attributes=_load_json_dict(row["attributes"]),
        created_at=_load_dt(row["created_at"]),
    )


def _row_to_eval_red_team_candidate(row: sqlite3.Row) -> EvalRedTeamCandidate:
    return EvalRedTeamCandidate(
        id=row["id"],
        suite=row["suite"],
        case_id=row["case_id"],
        name=row["name"],
        input=row["input"],
        attack_surface=row["attack_surface"],
        status=EvalRedTeamCandidateStatus(row["status"]),
        case_json=_load_json_dict(row["case_json"]),
        created_at=_load_dt(row["created_at"]),
        updated_at=_load_dt(row["updated_at"]),
        approved_at=_load_dt(row["approved_at"]) if row["approved_at"] else None,
    )


_repository: JobRepository | None = None


def get_job_repository() -> JobRepository:
    global _repository
    if _repository is None:
        _repository = SQLiteJobRepository()
    return _repository


def set_job_repository(repository: JobRepository | None) -> None:
    global _repository
    _repository = repository
