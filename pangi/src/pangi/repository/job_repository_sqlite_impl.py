from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from pangi.domain.models import AgentJob, CodexRun, JobStatus, JobType, SlackThread, utc_now
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

    def create_job(
        self,
        *,
        event_id: str,
        slack_thread: SlackThread,
        requester_user_id: str,
        prompt: str,
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
                        id, event_id, slack_thread_id, slack_team_id, slack_channel_id,
                        slack_thread_ts, requester_user_id, job_type, status, repo_key,
                        prompt, worktree_path, stdout, stderr, error_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        job_id,
                        event_id,
                        slack_thread.id,
                        slack_thread.team_id,
                        slack_thread.channel_id,
                        slack_thread.thread_ts,
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
                    stdout = COALESCE(?, stdout),
                    stderr = COALESCE(?, stderr),
                    error_message = COALESCE(?, error_message),
                    updated_at = ?
                WHERE id = ?
                """,
                (worktree_path, stdout, stderr, error_message, _dump_dt(now), job_id),
            )
            job = self._get_job(conn, job_id)
            return job

    def append_codex_run(
        self,
        *,
        job_id: str,
        mode: str,
        command: str,
        prompt: str,
        stdout: str | None = None,
        stderr: str | None = None,
        exit_code: int | None = None,
        timed_out: bool = False,
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
                    id, job_id, mode, command, prompt, stdout, stderr, exit_code,
                    timed_out, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    job_id,
                    mode,
                    command,
                    prompt,
                    stdout,
                    stderr,
                    exit_code,
                    1 if timed_out else 0,
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(team_id, channel_id, thread_ts)
                );

                CREATE TABLE IF NOT EXISTS agent_jobs (
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

                CREATE TABLE IF NOT EXISTS codex_runs (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    command TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    stdout TEXT,
                    stderr TEXT,
                    exit_code INTEGER,
                    timed_out INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(job_id) REFERENCES agent_jobs(id)
                );
                """
            )

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

    def _get_job(self, conn: sqlite3.Connection, job_id: str) -> AgentJob:
        row = conn.execute("SELECT * FROM agent_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _row_to_agent_job(row)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _normalize_limit(limit: int) -> int:
    return min(max(limit, 1), 200)


def _dump_dt(value: object) -> str:
    if not hasattr(value, "isoformat"):
        raise TypeError("Expected datetime-like value")
    return value.isoformat()


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
        created_at=_load_dt(row["created_at"]),
        updated_at=_load_dt(row["updated_at"]),
    )


def _row_to_agent_job(row: sqlite3.Row) -> AgentJob:
    return AgentJob(
        id=row["id"],
        event_id=row["event_id"],
        slack_thread_id=row["slack_thread_id"],
        slack_team_id=row["slack_team_id"],
        slack_channel_id=row["slack_channel_id"],
        slack_thread_ts=row["slack_thread_ts"],
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
        mode=row["mode"],
        command=row["command"],
        prompt=row["prompt"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        exit_code=row["exit_code"],
        timed_out=bool(row["timed_out"]),
        started_at=_load_dt(row["started_at"]),
        finished_at=_load_dt(row["finished_at"]) if row["finished_at"] else None,
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
