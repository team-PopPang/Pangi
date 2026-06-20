from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pangi.domain.models import JobStatus
from pangi.domain.policies import redact_secrets, truncate_text
from pangi.repository import DuplicateEventError, JobRepository
from pangi.usecase.request_decision import NEEDS_REPO_MESSAGE, RequestClassification
from pangi.usecase.ports import ChatResponder, JobQueue, RequestOrchestrator, SlackNotifier


IN_PROGRESS_REACTION_NAME = "eyes"
SUCCESS_REACTION_NAME = "white_check_mark"
FAILURE_REACTION_NAME = "x"
CHAT_REPLY_MAX_CHARS = 3500
logger = logging.getLogger(__name__)
BackgroundRunner = Callable[[Awaitable[None]], None]


def _default_background_runner(task: Awaitable[None]) -> None:
    asyncio.create_task(task)


@dataclass(frozen=True)
class SubmitSlackRequestInput:
    team_id: str
    channel_id: str
    user_id: str
    text: str
    thread_ts: str
    event_id: str
    message_ts: str


@dataclass(frozen=True)
class SubmitSlackRequestResult:
    job_id: str | None
    job_status: JobStatus | None
    classification: RequestClassification
    duplicate: bool = False


class SubmitSlackRequestUseCase:
    def __init__(
        self,
        *,
        repository: JobRepository,
        job_queue: JobQueue,
        slack_notifier: SlackNotifier,
        request_orchestrator: RequestOrchestrator,
        chat_responder: ChatResponder,
        allowed_repo_keys: tuple[str, ...],
        background_runner: BackgroundRunner = _default_background_runner,
    ) -> None:
        self._repository = repository
        self._job_queue = job_queue
        self._slack_notifier = slack_notifier
        self._request_orchestrator = request_orchestrator
        self._chat_responder = chat_responder
        self._allowed_repo_keys = allowed_repo_keys
        self._background_runner = background_runner

    async def execute(self, request: SubmitSlackRequestInput) -> SubmitSlackRequestResult:
        decision = await self._request_orchestrator.decide(
            text=request.text,
            allowed_repo_keys=self._allowed_repo_keys,
        )
        if decision.kind == RequestClassification.CODEX_CHAT:
            await self._add_in_progress_reaction(request)
            self._background_runner(self._post_chat_response(request))
            return SubmitSlackRequestResult(
                job_id=None,
                job_status=None,
                classification=decision.kind,
            )

        if not decision.should_create_job:
            await self._post_policy_message(request, decision.reply_text or NEEDS_REPO_MESSAGE)
            return SubmitSlackRequestResult(
                job_id=None,
                job_status=None,
                classification=decision.kind,
            )

        if decision.kind != RequestClassification.REPO_ANALYSIS or decision.repo_key is None:
            await self._post_policy_message(request, NEEDS_REPO_MESSAGE)
            return SubmitSlackRequestResult(
                job_id=None,
                job_status=None,
                classification=RequestClassification.NEEDS_REPO,
            )

        await self._add_in_progress_reaction(request)
        thread = self._repository.get_or_create_thread(
            team_id=request.team_id,
            channel_id=request.channel_id,
            thread_ts=request.thread_ts,
        )
        try:
            job = self._repository.create_job(
                event_id=request.event_id,
                slack_thread=thread,
                requester_user_id=request.user_id,
                prompt=request.text,
                slack_message_ts=request.message_ts or None,
                repo_key=decision.repo_key,
            )
        except DuplicateEventError:
            existing_job = self._repository.find_job_by_event_id(request.event_id)
            if existing_job is None:
                raise
            return SubmitSlackRequestResult(
                job_id=existing_job.id,
                job_status=existing_job.status,
                classification=decision.kind,
                duplicate=True,
            )

        await self._post_acceptance_message(request, job.id)
        await self._job_queue.enqueue(job.id)
        return SubmitSlackRequestResult(
            job_id=job.id,
            job_status=job.status,
            classification=decision.kind,
        )

    async def _add_in_progress_reaction(self, request: SubmitSlackRequestInput) -> None:
        if not request.message_ts:
            return
        try:
            await self._slack_notifier.add_reaction(
                channel_id=request.channel_id,
                message_ts=request.message_ts,
                name=IN_PROGRESS_REACTION_NAME,
            )
        except Exception as error:
            logger.warning("Failed to add Slack reaction: %s", error)

    async def _replace_in_progress_reaction(self, request: SubmitSlackRequestInput, *, name: str) -> None:
        if not request.message_ts:
            return
        try:
            await self._slack_notifier.remove_reaction(
                channel_id=request.channel_id,
                message_ts=request.message_ts,
                name=IN_PROGRESS_REACTION_NAME,
            )
        except Exception as error:
            logger.warning("Failed to remove Slack reaction: %s", error)

        try:
            await self._slack_notifier.add_reaction(
                channel_id=request.channel_id,
                message_ts=request.message_ts,
                name=name,
            )
        except Exception as error:
            logger.warning("Failed to add Slack reaction: %s", error)

    async def _post_acceptance_message(self, request: SubmitSlackRequestInput, job_id: str) -> None:
        try:
            await self._slack_notifier.post_message(
                channel_id=request.channel_id,
                thread_ts=request.thread_ts,
                text=f"팡이가 요청을 접수했습니다. job_id: {job_id}",
            )
        except Exception as error:
            logger.warning("Failed to post Slack message: %s", error)

    async def _post_policy_message(self, request: SubmitSlackRequestInput, text: str) -> None:
        if not text:
            return
        safe_text = truncate_text(
            redact_secrets(text),
            max_chars=CHAT_REPLY_MAX_CHARS,
        )
        try:
            await self._slack_notifier.post_message(
                channel_id=request.channel_id,
                thread_ts=request.thread_ts,
                text=safe_text,
            )
        except Exception as error:
            logger.warning("Failed to post Slack policy message: %s", error)

    async def _post_chat_response(self, request: SubmitSlackRequestInput) -> None:
        succeeded = True
        try:
            response_text = await self._chat_responder.respond(
                text=request.text,
                user_id=request.user_id,
                channel_id=request.channel_id,
                thread_ts=request.thread_ts,
            )
        except Exception as error:
            logger.warning("Failed to generate chat response: %s", error)
            succeeded = False
            response_text = "팡이 대화 응답 생성에 실패했습니다."

        safe_text = truncate_text(
            redact_secrets(response_text),
            max_chars=CHAT_REPLY_MAX_CHARS,
        )
        try:
            await self._slack_notifier.post_message(
                channel_id=request.channel_id,
                thread_ts=request.thread_ts,
                text=safe_text,
            )
            await self._replace_in_progress_reaction(
                request,
                name=SUCCESS_REACTION_NAME if succeeded else FAILURE_REACTION_NAME,
            )
        except Exception as error:
            logger.warning("Failed to post Slack chat response: %s", error)
            await self._replace_in_progress_reaction(request, name=FAILURE_REACTION_NAME)
