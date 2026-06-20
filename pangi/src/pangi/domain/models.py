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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SlackThread:
    id: str
    team_id: str
    channel_id: str
    thread_ts: str
    last_job_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AgentJob:
    id: str
    event_id: str
    slack_thread_id: str
    slack_team_id: str
    slack_channel_id: str
    slack_thread_ts: str
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
    mode: str
    command: str
    prompt: str
    stdout: str | None
    stderr: str | None
    exit_code: int | None
    timed_out: bool
    started_at: datetime
    finished_at: datetime | None
