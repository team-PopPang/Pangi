from __future__ import annotations

import logging
from dataclasses import dataclass

from pangi.domain.models import JobStatus
from pangi.repository import DuplicateEventError, JobRepository
from pangi.usecase.ports import JobQueue, SlackNotifier


IN_PROGRESS_REACTION_NAME = "eyes"
logger = logging.getLogger(__name__)


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
    job_id: str
    job_status: JobStatus
    duplicate: bool = False


class SubmitSlackRequestUseCase:
    def __init__(
        self,
        *,
        repository: JobRepository,
        job_queue: JobQueue,
        slack_notifier: SlackNotifier,
    ) -> None:
        self._repository = repository
        self._job_queue = job_queue
        self._slack_notifier = slack_notifier

    async def execute(self, request: SubmitSlackRequestInput) -> SubmitSlackRequestResult:
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
            )
        except DuplicateEventError:
            existing_job = self._repository.find_job_by_event_id(request.event_id)
            if existing_job is None:
                raise
            return SubmitSlackRequestResult(
                job_id=existing_job.id,
                job_status=existing_job.status,
                duplicate=True,
            )

        await self._post_acceptance_message(request, job.id)
        await self._job_queue.enqueue(job.id)
        return SubmitSlackRequestResult(job_id=job.id, job_status=job.status)

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

    async def _post_acceptance_message(self, request: SubmitSlackRequestInput, job_id: str) -> None:
        try:
            await self._slack_notifier.post_message(
                channel_id=request.channel_id,
                thread_ts=request.thread_ts,
                text=f"팡이가 요청을 접수했습니다. job_id: {job_id}",
            )
        except Exception as error:
            logger.warning("Failed to post Slack message: %s", error)
