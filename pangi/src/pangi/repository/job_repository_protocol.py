from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pangi.domain.models import AgentJob, CodexRun, JobStatus, JobType, SlackThread


DEFAULT_REPO_KEY = "PopPang-iOS"


class JobRepository(Protocol):
    """Slack thread, agent job, Codex run을 저장하고 조회하는 저장소 계약.

    usecase는 SQLite 세부 구현이 아니라 이 프로토콜에만 의존한다.
    구현체는 Slack retry가 중복 job을 만들지 않도록 event id 유일성을
    반드시 보장해야 한다.
    """

    def get_or_create_thread(self, *, team_id: str, channel_id: str, thread_ts: str) -> SlackThread:
        """Slack thread 식별자로 thread record를 조회하거나 새로 만든다."""
        ...

    def create_job(
        self,
        *,
        event_id: str,
        slack_thread: SlackThread,
        requester_user_id: str,
        prompt: str,
        slack_message_ts: str | None = None,
        job_type: JobType = JobType.ANALYZE,
        repo_key: str = DEFAULT_REPO_KEY,
    ) -> AgentJob:
        """Slack 요청 하나를 queued 상태의 agent job으로 저장한다.

        구현체는 `event_id` 유일성을 보장해야 하며, 중복이면
        `DuplicateEventError`에 해당하는 저장소별 예외를 발생시킨다.
        """
        ...

    def get_job(self, job_id: str) -> AgentJob | None:
        """job id로 agent job을 조회하고, 없으면 None을 반환한다."""
        ...

    def find_job_by_event_id(self, event_id: str) -> AgentJob | None:
        """Slack event id로 기존 job을 조회해 retry 중복 처리를 돕는다."""
        ...

    def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error_message: str | None = None,
    ) -> AgentJob:
        """job 상태와 선택적 error message를 갱신한 뒤 최신 job을 반환한다."""
        ...

    def update_job_result(
        self,
        job_id: str,
        *,
        worktree_path: str | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
        error_message: str | None = None,
    ) -> AgentJob:
        """job 실행 중 생성된 worktree 경로와 출력 결과를 저장한다."""
        ...

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
        """job 안에서 실행된 Codex 실행 기록을 저장하고 반환한다."""
        ...

    def list_threads(self, *, limit: int = 50) -> list[SlackThread]:
        """관리자 확인용으로 최근 Slack thread 목록을 반환한다."""
        ...

    def list_jobs(self, *, limit: int = 50) -> list[AgentJob]:
        """관리자 확인용으로 최근 agent job 목록을 반환한다."""
        ...

    def list_codex_runs(self, *, limit: int = 50) -> list[CodexRun]:
        """관리자 확인용으로 최근 Codex 실행 기록 목록을 반환한다."""
        ...
