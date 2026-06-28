from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    WAITING_APPROVAL = "waiting_approval"
    REJECTED = "rejected"


class JobType(StrEnum):
    ANALYZE = "analyze"
    EDIT_REQUESTED = "edit_requested"
    PR_SUMMARY = "pr_summary"
    TROUBLESHOOTING = "troubleshooting"
    XCODEBUILD_FAILURE = "xcodebuild_failure"


class ThreadMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class CodexSessionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    ARCHIVED = "archived"
    ARCHIVE_FAILED = "archive_failed"


class ScheduleType(StrEnum):
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"


class ScheduleRunStatus(StrEnum):
    CLAIMED = "claimed"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EvalRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EvalCaseStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class EvalRedTeamCandidateStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SlackThread:
    id: str
    team_id: str
    channel_id: str
    thread_ts: str
    last_job_id: str | None
    active_codex_session_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ThreadMessage:
    id: str
    slack_thread_id: str
    role: ThreadMessageRole
    text: str
    message_ts: str | None
    event_id: str | None
    source_job_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class AgentJob:
    id: str
    event_id: str
    slack_thread_id: str
    codex_session_id: str | None
    slack_team_id: str
    slack_channel_id: str
    slack_thread_ts: str
    slack_message_ts: str | None
    requester_user_id: str
    job_type: JobType
    status: JobStatus
    repo_key: str
    prompt: str
    worktree_path: str | None
    stdout: str | None
    stderr: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CodexRun:
    id: str
    job_id: str
    codex_session_id: str | None
    mode: str
    command: str
    prompt: str
    stdout: str | None
    stderr: str | None
    exit_code: int | None
    timed_out: bool
    workspace_path: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class CodexSession:
    id: str
    slack_thread_id: str
    codex_thread_id: str
    workspace_path: str
    status: CodexSessionStatus
    last_used_at: datetime
    expires_at: datetime
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ScheduledTask:
    id: str
    name: str
    enabled: bool
    team_id: str
    channel_id: str
    requester_user_id: str
    prompt: str
    schedule_type: ScheduleType
    timezone: str
    time_of_day: str | None
    days_of_week: str | None
    run_at: datetime | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ScheduledTaskRun:
    id: str
    scheduled_task_id: str
    scheduled_for: datetime
    status: ScheduleRunStatus
    event_id: str
    slack_thread_ts: str | None
    job_id: str | None
    classification: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EvalCaseDefinition:
    id: str
    suite: str
    case_id: str
    name: str
    tags: tuple[str, ...]
    case_json: dict[str, object]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EvalRun:
    id: str
    suite: str
    mode: str
    status: EvalRunStatus
    total_count: int
    passed_count: int
    failed_count: int
    prompt_fingerprint: str | None
    model_fingerprint: str | None
    provider_fingerprint: str | None
    started_at: datetime
    finished_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EvalCaseResultRecord:
    id: str
    eval_run_id: str
    suite: str
    case_id: str
    name: str
    status: EvalCaseStatus
    classification: str
    job_id: str | None
    job_repo_key: str | None
    failures: tuple[str, ...]
    slack_messages: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True)
class EvalTraceEventRecord:
    id: str
    eval_case_result_id: str
    event_index: int
    name: str
    attributes: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class EvalRedTeamCandidate:
    id: str
    suite: str
    case_id: str
    name: str
    input: str
    attack_surface: str
    status: EvalRedTeamCandidateStatus
    case_json: dict[str, object]
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None
