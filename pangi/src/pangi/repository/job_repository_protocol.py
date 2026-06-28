from __future__ import annotations

from datetime import datetime
from typing import Protocol

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
)


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
        """Slack thread 안의 user/assistant turn을 저장하고 반환한다.

        `event_id`가 있는 user message는 Slack retry에도 중복 저장되지 않아야 한다.
        """
        ...

    def list_thread_messages(self, slack_thread_id: str, *, limit: int = 20) -> list[ThreadMessage]:
        """prompt context에 사용할 최근 thread message를 오래된 순서로 반환한다."""
        ...

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
        codex_session_id: str | None = None,
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
        """job 안에서 실행된 Codex 실행 기록을 저장하고 반환한다."""
        ...

    def get_active_codex_session(self, slack_thread_id: str) -> CodexSession | None:
        """Slack thread의 현재 활성 Codex session을 조회한다."""
        ...

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
        """Slack thread의 활성 Codex session을 생성하고 thread에 연결한다."""
        ...

    def update_codex_session_activity(
        self,
        codex_session_id: str,
        *,
        status: CodexSessionStatus | None = None,
        last_used_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> CodexSession:
        """Codex session의 상태와 activity timestamp를 갱신한다."""
        ...

    def archive_codex_session(
        self,
        codex_session_id: str,
        *,
        status: CodexSessionStatus,
        archived_at: datetime | None,
    ) -> CodexSession:
        """Codex session을 archive 상태로 바꾸고 thread의 active 연결을 해제한다."""
        ...

    def list_expired_active_codex_sessions(self, *, now: datetime, limit: int = 100) -> list[CodexSession]:
        """idle timeout을 지난 활성 Codex session 목록을 반환한다."""
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
        """예약 작업 정의를 저장한다."""
        ...

    def set_scheduled_task_enabled(self, task_id: str, *, enabled: bool) -> ScheduledTask:
        """예약 작업 활성 상태를 변경한다."""
        ...

    def get_scheduled_task(self, task_id: str) -> ScheduledTask | None:
        """예약 작업 id로 정의를 조회한다."""
        ...

    def list_scheduled_tasks(self, *, limit: int = 50) -> list[ScheduledTask]:
        """관리자 확인용으로 예약 작업 목록을 반환한다."""
        ...

    def list_due_scheduled_tasks(self, *, now: datetime, limit: int = 20) -> list[ScheduledTask]:
        """실행 예정 시간이 지난 활성 예약 작업 목록을 반환한다."""
        ...

    def claim_scheduled_task_run(
        self,
        *,
        task_id: str,
        scheduled_for: datetime,
        next_run_at: datetime | None,
    ) -> ScheduledTaskRun | None:
        """예약 실행을 중복 없이 claim하고 다음 실행 시각을 갱신한다."""
        ...

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
        """예약 실행 결과를 갱신한다."""
        ...

    def list_scheduled_task_runs(self, *, limit: int = 50) -> list[ScheduledTaskRun]:
        """관리자 확인용으로 최근 예약 실행 기록을 반환한다."""
        ...

    def upsert_eval_case(
        self,
        *,
        suite: str,
        case_id: str,
        name: str,
        tags: tuple[str, ...],
        case_json: dict[str, object],
    ) -> EvalCaseDefinition:
        """Eval case 정의 snapshot을 저장하거나 최신 내용으로 갱신한다."""
        ...

    def list_eval_cases(self, *, limit: int = 100) -> list[EvalCaseDefinition]:
        """관리자 확인용으로 등록된 Eval case 정의를 반환한다."""
        ...

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
        """Eval 실행 요약을 저장한다."""
        ...

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
        """Eval run 안의 case 결과를 저장한다."""
        ...

    def append_eval_trace_event(
        self,
        *,
        eval_case_result_id: str,
        event_index: int,
        name: str,
        attributes: dict[str, object],
    ) -> EvalTraceEventRecord:
        """case 결과에 연결되는 trace event를 저장한다."""
        ...

    def list_eval_runs(self, *, limit: int = 50) -> list[EvalRun]:
        """관리자 확인용으로 최근 Eval run 목록을 반환한다."""
        ...

    def list_eval_case_results(
        self,
        *,
        eval_run_id: str | None = None,
        limit: int = 100,
    ) -> list[EvalCaseResultRecord]:
        """최근 또는 특정 Eval run의 case 결과를 반환한다."""
        ...

    def list_eval_trace_events(
        self,
        *,
        eval_case_result_id: str | None = None,
        limit: int = 200,
    ) -> list[EvalTraceEventRecord]:
        """최근 또는 특정 case 결과의 trace event를 반환한다."""
        ...

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
        """Red Team Agent가 만든 후보 case를 draft 상태로 저장한다."""
        ...

    def set_eval_red_team_candidate_status(
        self,
        candidate_id: str,
        *,
        status: EvalRedTeamCandidateStatus,
    ) -> EvalRedTeamCandidate:
        """Red Team 후보 case의 검토 상태를 갱신한다."""
        ...

    def list_eval_red_team_candidates(
        self,
        *,
        status: EvalRedTeamCandidateStatus | None = None,
        limit: int = 50,
    ) -> list[EvalRedTeamCandidate]:
        """관리자 확인용으로 Red Team 후보 case 목록을 반환한다."""
        ...
