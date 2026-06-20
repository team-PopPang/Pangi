from __future__ import annotations

import json
import logging

from pangi.domain.models import AgentJob
from pangi.domain.policies import redact_secrets, truncate_text
from pangi.repository import JobRepository
from pangi.usecase.build_prompt import build_read_only_analysis_prompt
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
    ) -> None:
        self._repository = repository
        self._worktree_manager = worktree_manager
        self._codex_runner = codex_runner
        self._slack_notifier = slack_notifier
        self._timeout_seconds = timeout_seconds

    async def execute(self, job: AgentJob) -> None:
        worktree = await self._worktree_manager.prepare_read_only_worktree(
            job_id=job.id,
            repo_key=job.repo_key,
        )
        self._repository.update_job_result(job.id, worktree_path=str(worktree.path))

        prompt = build_read_only_analysis_prompt(job)
        result = await self._codex_runner.run_read_only(
            worktree_path=worktree.path,
            prompt=prompt,
            timeout_seconds=self._timeout_seconds,
        )
        safe_stdout = redact_secrets(result.stdout)
        safe_stderr = redact_secrets(result.stderr)
        self._record_codex_result(job=job, prompt=prompt, result=result, stdout=safe_stdout, stderr=safe_stderr)

        if result.timed_out:
            message = "Codex read-only analysis timed out"
            self._repository.update_job_result(job.id, error_message=message)
            await self._post_timeout(job, message)
            raise AnalysisJobTimedOut(message)

        if result.exit_code != 0:
            message = self._failure_summary(result.exit_code, safe_stdout, safe_stderr)
            self._repository.update_job_result(job.id, error_message=message)
            await self._post_failure(job, message)
            raise AnalysisJobFailed(message)

        self._repository.update_job_result(job.id, stdout=safe_stdout, stderr=safe_stderr)
        await self._post_success(job, safe_stdout)

    def _record_codex_result(
        self,
        *,
        job: AgentJob,
        prompt: str,
        result: CodexExecutionResult,
        stdout: str,
        stderr: str,
    ) -> None:
        self._repository.append_codex_run(
            job_id=job.id,
            mode="read-only",
            command=_display_command(result.command),
            prompt=prompt,
            stdout=stdout,
            stderr=stderr,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            started_at=result.started_at,
            finished_at=result.finished_at,
        )
        self._repository.update_job_result(job.id, stdout=stdout, stderr=stderr)

    async def _post_success(self, job: AgentJob, stdout: str) -> None:
        body = truncate_text(stdout.strip() or "분석 결과 출력이 비어 있습니다.", max_chars=SLACK_RESULT_MAX_CHARS)
        await self._slack_notifier.post_message(
            channel_id=job.slack_channel_id,
            thread_ts=job.slack_thread_ts,
            text=f"팡이가 read-only 분석을 완료했습니다. job_id: {job.id}\n\n{body}",
        )
        await self._replace_in_progress_reaction(job, name=SUCCESS_REACTION_NAME)

    async def _post_failure(self, job: AgentJob, message: str) -> None:
        await self._slack_notifier.post_message(
            channel_id=job.slack_channel_id,
            thread_ts=job.slack_thread_ts,
            text=f"팡이 read-only 분석이 실패했습니다. job_id: {job.id}\n\n{message}",
        )
        await self._replace_in_progress_reaction(job, name=FAILURE_REACTION_NAME)

    async def _post_timeout(self, job: AgentJob, message: str) -> None:
        await self._slack_notifier.post_message(
            channel_id=job.slack_channel_id,
            thread_ts=job.slack_thread_ts,
            text=f"팡이 read-only 분석 시간이 초과되었습니다. job_id: {job.id}\n\n{message}",
        )
        await self._replace_in_progress_reaction(job, name=FAILURE_REACTION_NAME)

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
        detail = truncate_text(detail, max_chars=SLACK_ERROR_MAX_CHARS)
        return f"Codex exited with code {exit_code}. {detail}"


def _display_command(command: tuple[str, ...]) -> str:
    display = list(command)
    if display:
        display[-1] = "{prompt}"
    return json.dumps(display, ensure_ascii=False)
