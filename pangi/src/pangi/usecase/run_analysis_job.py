from __future__ import annotations

import json
import logging
from pangi.domain.models import AgentJob, ThreadMessageRole
from pangi.domain.policies import redact_secrets
from pangi.repository import JobRepository
from pangi.usecase.codex_session import CodexSessionService
from pangi.usecase.build_prompt import build_read_only_analysis_prompt
from pangi.usecase.output_guardrail import (
    classify_error_kind,
    next_action_for_error,
    prepare_error_markdown,
    prepare_output_markdown,
)
from pangi.usecase.ports import CodexExecutionResult, CodexRunner, SlackNotifier, WorktreeManager


SLACK_RESULT_MAX_CHARS = 3500
SLACK_ERROR_MAX_CHARS = 1200
IN_PROGRESS_REACTION_NAME = "eyes"
SUCCESS_REACTION_NAME = "white_check_mark"
FAILURE_REACTION_NAME = "x"
logger = logging.getLogger(__name__)


class AnalysisJobFailed(RuntimeError):
    pass


class AnalysisJobTimedOut(TimeoutError):
    pass


class RunAnalysisJobUseCase:
    def __init__(
        self,
        *,
        repository: JobRepository,
        worktree_manager: WorktreeManager,
        codex_runner: CodexRunner,
        slack_notifier: SlackNotifier,
        timeout_seconds: float,
        session_idle_timeout_seconds: int,
    ) -> None:
        self._repository = repository
        self._worktree_manager = worktree_manager
        self._codex_runner = codex_runner
        self._slack_notifier = slack_notifier
        self._timeout_seconds = timeout_seconds
        self._session_service = CodexSessionService(
            repository=repository,
            codex_runner=codex_runner,
            idle_timeout_seconds=session_idle_timeout_seconds,
        )

    async def execute(self, job: AgentJob) -> None:
        prepared_session = await self._session_service.prepare_for_turn(job.slack_thread_id)
        if prepared_session.expired_previous_session:
            await self._worktree_manager.cleanup_thread_workspace(slack_thread_id=job.slack_thread_id)
        workspace = await self._worktree_manager.prepare_thread_repo_workspace(
            slack_thread_id=job.slack_thread_id,
            repo_key=job.repo_key,
        )
        self._repository.update_job_result(job.id, worktree_path=str(workspace.repo_path))

        prompt = build_read_only_analysis_prompt(job, repo_path=str(workspace.repo_path))
        result = await self._codex_runner.run_read_only(
            workspace_path=workspace.workspace_path,
            prompt=prompt,
            timeout_seconds=self._timeout_seconds,
            resume_session_id=prepared_session.active_session.codex_thread_id if prepared_session.active_session else None,
        )
        safe_stdout = redact_secrets(result.stdout)
        safe_stderr = redact_secrets(result.stderr)
        session = self._session_service.record_turn_result(
            slack_thread_id=job.slack_thread_id,
            workspace_path=workspace.workspace_path,
            existing_session=prepared_session.active_session,
            result=result,
        )
        self._repository.update_job_result(
            job.id,
            worktree_path=str(workspace.repo_path),
            codex_session_id=session.id,
        )
        self._record_codex_result(
            job=job,
            codex_session_id=session.id,
            prompt=prompt,
            result=result,
            stdout=safe_stdout,
            stderr=safe_stderr,
        )

        if result.timed_out:
            message = "Codex read-only analysis timed out"
            self._repository.update_job_result(job.id, error_message=message)
            await self._post_timeout(job, message, session_notice=prepared_session.expired_previous_session)
            raise AnalysisJobTimedOut(message)

        if result.exit_code != 0:
            message = self._failure_summary(result.exit_code, safe_stdout, safe_stderr)
            self._repository.update_job_result(job.id, error_message=message)
            await self._post_failure(job, message, session_notice=prepared_session.expired_previous_session)
            raise AnalysisJobFailed(message)

        self._repository.update_job_result(job.id, stdout=safe_stdout, stderr=safe_stderr)
        self._repository.update_job_result(job.id, error_message=None)
        await self._post_success(
            job,
            safe_stdout,
            session_notice=prepared_session.expired_previous_session,
            codex_session_id=session.codex_thread_id,
        )

    def _record_codex_result(
        self,
        *,
        job: AgentJob,
        codex_session_id: str | None,
        prompt: str,
        result: CodexExecutionResult,
        stdout: str,
        stderr: str,
    ) -> None:
        self._repository.append_codex_run(
            job_id=job.id,
            codex_session_id=codex_session_id,
            mode="read-only",
            command=_display_command(result.command),
            prompt=prompt,
            stdout=stdout,
            stderr=stderr,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            workspace_path=result.workspace_path,
            started_at=result.started_at,
            finished_at=result.finished_at,
        )
        self._repository.update_job_result(job.id, stdout=stdout, stderr=stderr)

    async def _post_success(
        self,
        job: AgentJob,
        stdout: str,
        *,
        session_notice: bool,
        codex_session_id: str,
    ) -> None:
        body = prepare_output_markdown(
            stdout,
            max_chars=SLACK_RESULT_MAX_CHARS,
            empty_fallback="분석 결과 출력이 비어 있습니다.",
        )
        session_prefix = (
            "이전 Codex session이 1시간 이상 비활성이라 새 session으로 다시 시작했습니다.\n\n"
            if session_notice
            else ""
        )
        message = prepare_output_markdown(
            f"{session_prefix}팡이가 read-only 분석을 완료했습니다. job_id: {job.id}\n"
            f"codex_session_id: {codex_session_id}\n\n{body}",
            max_chars=SLACK_RESULT_MAX_CHARS,
        )
        await self._slack_notifier.post_message(
            channel_id=job.slack_channel_id,
            thread_ts=job.slack_thread_ts,
            text=message,
        )
        self._record_assistant_message(job, message)
        await self._replace_in_progress_reaction(job, name=SUCCESS_REACTION_NAME)

    async def _post_failure(self, job: AgentJob, message: str, *, session_notice: bool) -> None:
        session_prefix = (
            "이전 Codex session이 1시간 이상 비활성이라 새 session으로 다시 시작했습니다.\n\n"
            if session_notice
            else ""
        )
        kind = classify_error_kind(message)
        response_text = prepare_error_markdown(
            stage="repo_analysis",
            kind=kind,
            summary="Codex read-only analysis failed",
            detail=message,
            next_action=next_action_for_error(stage="repo_analysis", kind=kind),
            job_id=job.id,
        )
        if session_prefix:
            response_text = f"{session_prefix}{response_text}"
        await self._slack_notifier.post_message(
            channel_id=job.slack_channel_id,
            thread_ts=job.slack_thread_ts,
            text=response_text,
        )
        self._record_assistant_message(job, response_text)
        await self._replace_in_progress_reaction(job, name=FAILURE_REACTION_NAME)

    async def _post_timeout(self, job: AgentJob, message: str, *, session_notice: bool) -> None:
        session_prefix = (
            "이전 Codex session이 1시간 이상 비활성이라 새 session으로 다시 시작했습니다.\n\n"
            if session_notice
            else ""
        )
        response_text = prepare_error_markdown(
            stage="repo_analysis",
            kind="timeout",
            summary="Codex read-only analysis timed out",
            detail=message,
            next_action=next_action_for_error(stage="repo_analysis", kind="timeout"),
            job_id=job.id,
        )
        if session_prefix:
            response_text = f"{session_prefix}{response_text}"
        await self._slack_notifier.post_message(
            channel_id=job.slack_channel_id,
            thread_ts=job.slack_thread_ts,
            text=response_text,
        )
        self._record_assistant_message(job, response_text)
        await self._replace_in_progress_reaction(job, name=FAILURE_REACTION_NAME)

    def _record_assistant_message(self, job: AgentJob, text: str) -> None:
        try:
            self._repository.append_thread_message(
                slack_thread_id=job.slack_thread_id,
                role=ThreadMessageRole.ASSISTANT,
                text=text,
                source_job_id=job.id,
            )
        except Exception as error:
            logger.warning("Failed to record Slack assistant message for job %s: %s", job.id, error)

    async def _replace_in_progress_reaction(self, job: AgentJob, *, name: str) -> None:
        if not job.slack_message_ts:
            return
        try:
            await self._slack_notifier.remove_reaction(
                channel_id=job.slack_channel_id,
                message_ts=job.slack_message_ts,
                name=IN_PROGRESS_REACTION_NAME,
            )
        except Exception as error:
            logger.warning("Failed to remove Slack reaction for job %s: %s", job.id, error)

        try:
            await self._slack_notifier.add_reaction(
                channel_id=job.slack_channel_id,
                message_ts=job.slack_message_ts,
                name=name,
            )
        except Exception as error:
            logger.warning("Failed to add Slack reaction for job %s: %s", job.id, error)

    def _failure_summary(self, exit_code: int | None, stdout: str, stderr: str) -> str:
        detail = stderr.strip() or stdout.strip() or "Codex output is empty"
        detail = detail or "Codex output is empty"
        return f"Codex exited with code {exit_code}. {detail}"


def _display_command(command: tuple[str, ...]) -> str:
    display = list(command)
    if display:
        display[-1] = "{prompt}"
    return json.dumps(display, ensure_ascii=False)
