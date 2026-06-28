from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable
from datetime import datetime, timezone

from pangi.config import get_settings
from pangi.domain.models import ScheduleRunStatus, ScheduledTask, ScheduledTaskRun, utc_now
from pangi.repository import JobRepository
from pangi.usecase.input_guardrail import route_request_input
from pangi.usecase.output_guardrail import prepare_output_markdown
from pangi.usecase.request_decision import RequestClassification
from pangi.usecase.scheduler import ScheduleValidationError, compute_next_run_after_claim
from pangi.usecase.submit_slack_request import SubmitSlackRequestInput, SubmitSlackRequestUseCase
from pangi.usecase.ports import ChatResponder, GitContextProvider, JobQueue, NotionContextProvider, RequestOrchestrator, SlackNotifier


logger = logging.getLogger(__name__)
GIT_REPO_KEYS_TIMEOUT_SECONDS = 5
_GIT_REPO_KEYS_OPTIONAL_CLASSIFICATIONS = frozenset(
    {
        RequestClassification.BLOCKED_WEB_ANALYSIS,
        RequestClassification.CODEX_CHAT,
        RequestClassification.GIT_CONTEXT_CHAT,
        RequestClassification.NOTION_CONTEXT_CHAT,
        RequestClassification.REPO_CATALOG,
        RequestClassification.UNSUPPORTED,
    }
)


class ScheduledTaskRunner:
    def __init__(
        self,
        *,
        repository: JobRepository,
        job_queue: JobQueue,
        slack_notifier: SlackNotifier,
        request_orchestrator: RequestOrchestrator,
        chat_responder: ChatResponder,
        notion_context_provider: NotionContextProvider | None = None,
        git_context_provider: GitContextProvider | None = None,
    ) -> None:
        self._repository = repository
        self._job_queue = job_queue
        self._slack_notifier = slack_notifier
        self._request_orchestrator = request_orchestrator
        self._chat_responder = chat_responder
        self._notion_context_provider = notion_context_provider
        self._git_context_provider = git_context_provider

    async def run_due(self, *, now: datetime | None = None, limit: int = 20) -> int:
        now = (now or utc_now()).astimezone(timezone.utc)
        claimed = 0
        for task in self._repository.list_due_scheduled_tasks(now=now, limit=limit):
            run = self._claim_due_task(task, now=now)
            if run is None:
                continue
            claimed += 1
            await self._submit_claimed_run(task=task, run=run)
        return claimed

    def _claim_due_task(self, task: ScheduledTask, *, now: datetime) -> ScheduledTaskRun | None:
        if task.next_run_at is None:
            return None
        try:
            next_run_at = compute_next_run_after_claim(task, after=now)
        except ScheduleValidationError as error:
            run = self._repository.claim_scheduled_task_run(
                task_id=task.id,
                scheduled_for=task.next_run_at,
                next_run_at=None,
            )
            if run is not None:
                self._repository.update_scheduled_task_run(
                    run.id,
                    status=ScheduleRunStatus.FAILED,
                    error_message=str(error),
                )
            return None
        return self._repository.claim_scheduled_task_run(
            task_id=task.id,
            scheduled_for=task.next_run_at,
            next_run_at=next_run_at,
        )

    async def _submit_claimed_run(self, *, task: ScheduledTask, run: ScheduledTaskRun) -> None:
        try:
            thread_ts = await self._post_schedule_root_message(task=task, run=run)
            self._repository.update_scheduled_task_run(
                run.id,
                status=ScheduleRunStatus.SUBMITTED,
                slack_thread_ts=thread_ts,
            )

            background_tasks: list[Awaitable[None]] = []
            use_case = SubmitSlackRequestUseCase(
                repository=self._repository,
                job_queue=self._job_queue,
                slack_notifier=self._slack_notifier,
                request_orchestrator=self._request_orchestrator,
                chat_responder=self._chat_responder,
                notion_context_provider=self._notion_context_provider,
                git_context_provider=self._git_context_provider,
                allowed_repo_keys=await self._allowed_repo_keys_for_text(task.prompt),
                local_repo_keys=get_settings().available_repo_keys(),
                background_runner=background_tasks.append,
            )
            result = await use_case.execute(
                SubmitSlackRequestInput(
                    team_id=task.team_id,
                    channel_id=task.channel_id,
                    user_id=task.requester_user_id,
                    text=task.prompt,
                    thread_ts=thread_ts,
                    event_id=run.event_id,
                    message_ts=thread_ts,
                    reaction_already_added=True,
                )
            )
            if background_tasks:
                await asyncio.gather(*background_tasks)

            self._repository.update_scheduled_task_run(
                run.id,
                status=ScheduleRunStatus.SUBMITTED if result.job_id else ScheduleRunStatus.SUCCEEDED,
                slack_thread_ts=thread_ts,
                job_id=result.job_id,
                classification=result.classification.value,
                error_message=None,
            )
        except Exception as error:
            logger.exception("Failed to submit scheduled task %s run %s", task.id, run.id)
            self._repository.update_scheduled_task_run(
                run.id,
                status=ScheduleRunStatus.FAILED,
                error_message=str(error),
            )

    async def _post_schedule_root_message(self, *, task: ScheduledTask, run: ScheduledTaskRun) -> str:
        scheduled_for = run.scheduled_for.astimezone(timezone.utc).isoformat(timespec="seconds")
        text = prepare_output_markdown(
            "팡이 예약 작업을 시작합니다.\n\n"
            f"schedule: {task.name}\n"
            f"scheduled_for: {scheduled_for}\n"
            f"run_id: {run.id}",
            max_chars=1200,
        )
        result = self._slack_notifier.post_message(channel_id=task.channel_id, text=text)
        thread_ts = await result if inspect.isawaitable(result) else result
        if not thread_ts:
            raise RuntimeError("Slack post_message did not return message ts")
        return thread_ts

    async def _allowed_repo_keys_for_text(self, text: str) -> tuple[str, ...]:
        local_repo_keys = get_settings().available_repo_keys()
        route = route_request_input(text, allowed_repo_keys=local_repo_keys)
        if (
            route.decision is not None
            and not route.needs_ai_orchestrator
            and route.decision.kind in _GIT_REPO_KEYS_OPTIONAL_CLASSIFICATIONS
        ):
            return local_repo_keys
        if self._git_context_provider is None:
            return local_repo_keys
        try:
            catalog = await asyncio.wait_for(
                self._git_context_provider.fetch_repo_catalog(local_repo_keys=local_repo_keys),
                timeout=GIT_REPO_KEYS_TIMEOUT_SECONDS,
            )
        except Exception as error:
            logger.warning("Failed to fetch Git MCP repo keys for scheduled task: %s", error)
            return local_repo_keys
        return tuple(item.name for item in catalog.items)


class InProcessScheduler:
    def __init__(
        self,
        *,
        runner: ScheduledTaskRunner,
        tick_seconds: float,
        due_limit: int = 20,
    ) -> None:
        if tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")
        self._runner = runner
        self._tick_seconds = tick_seconds
        self._due_limit = due_limit
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="pangi-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def run_once(self) -> int:
        return await self._runner.run_due(limit=self._due_limit)

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("Failed to run scheduled task tick")
            await asyncio.sleep(self._tick_seconds)
