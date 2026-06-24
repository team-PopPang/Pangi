from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from pangi.domain.models import (
    AgentJob,
    CodexRun,
    CodexSession,
    CodexSessionStatus,
    JobStatus,
    JobType,
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


_repository: JobRepository | None = None


def get_job_repository() -> JobRepository:
    global _repository
    if _repository is None:
        _repository = SQLiteJobRepository()
    return _repository


def set_job_repository(repository: JobRepository | None) -> None:
    global _repository
    _repository = repository
