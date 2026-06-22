from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pangi.domain.models import JobStatus
from pangi.repository import DuplicateEventError, JobRepository
from pangi.usecase.git_context import (
    GitContextAccessDeniedError,
    GitContextDisabledError,
    GitRepoCatalog,
    GitRepoCatalogItem,
    build_git_context_prompt,
    format_repo_catalog_response,
)
from pangi.usecase.notion_context import (
    NotionContextAccessDeniedError,
    NotionContextDisabledError,
    build_notion_context_prompt,
)
from pangi.usecase.output_guardrail import prepare_output_markdown
from pangi.usecase.request_decision import (
    GIT_CONTEXT_ACCESS_DENIED_MESSAGE,
    GIT_CONTEXT_DISABLED_MESSAGE,
    NEEDS_REPO_MESSAGE,
    NOTION_CONTEXT_ACCESS_DENIED_MESSAGE,
    NOTION_CONTEXT_DISABLED_MESSAGE,
    RequestClassification,
    build_needs_repo_message,
)
from pangi.usecase.ports import (
    ChatResponder,
    GitContextProvider,
    JobQueue,
    NotionContextProvider,
    RequestOrchestrator,
    SlackNotifier,
)


IN_PROGRESS_REACTION_NAME = "eyes"
SUCCESS_REACTION_NAME = "white_check_mark"
FAILURE_REACTION_NAME = "x"
CHAT_REPLY_MAX_CHARS = 3500
CLASSIFICATION_FAILURE_MESSAGE = "팡이 요청 분류가 지연되어 실패했습니다. 잠시 후 다시 요청해주세요."
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
        notion_context_provider: NotionContextProvider | None = None,
        git_context_provider: GitContextProvider | None = None,
        background_runner: BackgroundRunner = _default_background_runner,
    ) -> None:
        self._repository = repository
        self._job_queue = job_queue
        self._slack_notifier = slack_notifier
        self._request_orchestrator = request_orchestrator
        self._chat_responder = chat_responder
        self._allowed_repo_keys = allowed_repo_keys
        self._notion_context_provider = notion_context_provider
        self._git_context_provider = git_context_provider
        self._background_runner = background_runner

    async def execute(self, request: SubmitSlackRequestInput) -> SubmitSlackRequestResult:
        await self._add_in_progress_reaction(request)
        try:
            decision = await self._request_orchestrator.decide(
                text=request.text,
                allowed_repo_keys=self._allowed_repo_keys,
            )
        except Exception:
            logger.exception("Failed to classify Slack request")
            await self._post_policy_message(request, CLASSIFICATION_FAILURE_MESSAGE)
            await self._replace_in_progress_reaction(request, name=FAILURE_REACTION_NAME)
            return SubmitSlackRequestResult(
                job_id=None,
                job_status=None,
                classification=RequestClassification.UNSUPPORTED,
            )

        if decision.kind == RequestClassification.CODEX_CHAT:
            self._background_runner(self._post_chat_response(request))
            return SubmitSlackRequestResult(
                job_id=None,
                job_status=None,
                classification=decision.kind,
            )

        if decision.kind == RequestClassification.NOTION_CONTEXT_CHAT:
            self._background_runner(self._post_notion_context_response(request))
            return SubmitSlackRequestResult(
                job_id=None,
                job_status=None,
                classification=decision.kind,
            )

        if decision.kind == RequestClassification.GIT_CONTEXT_CHAT:
            self._background_runner(self._post_git_context_response(request))
            return SubmitSlackRequestResult(
                job_id=None,
                job_status=None,
                classification=decision.kind,
            )

        if decision.kind == RequestClassification.REPO_CATALOG:
            self._background_runner(self._post_repo_catalog_response(request))
            return SubmitSlackRequestResult(
                job_id=None,
                job_status=None,
                classification=decision.kind,
            )

        if not decision.should_create_job:
            await self._post_policy_message(request, self._reply_text_for_non_job_decision(decision))
            await self._replace_in_progress_reaction(request, name=SUCCESS_REACTION_NAME)
            return SubmitSlackRequestResult(
                job_id=None,
                job_status=None,
                classification=decision.kind,
            )

        if decision.kind != RequestClassification.REPO_ANALYSIS or decision.repo_key is None:
            await self._post_policy_message(request, build_needs_repo_message(self._allowed_repo_keys))
            await self._replace_in_progress_reaction(request, name=SUCCESS_REACTION_NAME)
            return SubmitSlackRequestResult(
                job_id=None,
                job_status=None,
                classification=RequestClassification.NEEDS_REPO,
            )

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

    def _reply_text_for_non_job_decision(self, decision) -> str:
        if decision.reply_text:
            return decision.reply_text
        if decision.kind == RequestClassification.NEEDS_REPO:
            return build_needs_repo_message(self._allowed_repo_keys)
        return NEEDS_REPO_MESSAGE

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
                text=prepare_output_markdown(f"팡이가 요청을 접수했습니다. job_id: {job_id}"),
            )
        except Exception as error:
            logger.warning("Failed to post Slack message: %s", error)

    async def _post_policy_message(self, request: SubmitSlackRequestInput, text: str) -> None:
        if not text:
            return
        safe_text = prepare_output_markdown(text, max_chars=CHAT_REPLY_MAX_CHARS)
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

        safe_text = prepare_output_markdown(response_text, max_chars=CHAT_REPLY_MAX_CHARS)
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

    async def _post_notion_context_response(self, request: SubmitSlackRequestInput) -> None:
        succeeded = True
        if self._notion_context_provider is None:
            response_text = NOTION_CONTEXT_DISABLED_MESSAGE
        else:
            try:
                context = await self._notion_context_provider.fetch_context(
                    text=request.text,
                    user_id=request.user_id,
                    channel_id=request.channel_id,
                    thread_ts=request.thread_ts,
                )
                response_text = await self._chat_responder.respond(
                    text=build_notion_context_prompt(user_text=request.text, context=context),
                    user_id=request.user_id,
                    channel_id=request.channel_id,
                    thread_ts=request.thread_ts,
                )
            except NotionContextDisabledError:
                response_text = NOTION_CONTEXT_DISABLED_MESSAGE
            except NotionContextAccessDeniedError:
                response_text = NOTION_CONTEXT_ACCESS_DENIED_MESSAGE
            except Exception as error:
                logger.warning("Failed to generate Notion context response: %s", error)
                succeeded = False
                response_text = "Notion 문서 확인에 실패했습니다. 잠시 후 다시 요청해주세요."

        safe_text = prepare_output_markdown(response_text, max_chars=CHAT_REPLY_MAX_CHARS)
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
            logger.warning("Failed to post Slack Notion context response: %s", error)
            await self._replace_in_progress_reaction(request, name=FAILURE_REACTION_NAME)

    async def _post_git_context_response(self, request: SubmitSlackRequestInput) -> None:
        succeeded = True
        if self._git_context_provider is None:
            response_text = GIT_CONTEXT_DISABLED_MESSAGE
        else:
            try:
                context = await self._git_context_provider.fetch_context(
                    text=request.text,
                    user_id=request.user_id,
                    channel_id=request.channel_id,
                    thread_ts=request.thread_ts,
                )
                response_text = await self._chat_responder.respond(
                    text=build_git_context_prompt(user_text=request.text, context=context),
                    user_id=request.user_id,
                    channel_id=request.channel_id,
                    thread_ts=request.thread_ts,
                )
            except GitContextDisabledError:
                response_text = GIT_CONTEXT_DISABLED_MESSAGE
            except GitContextAccessDeniedError:
                response_text = GIT_CONTEXT_ACCESS_DENIED_MESSAGE
            except Exception as error:
                logger.warning("Failed to generate Git context response: %s", error)
                succeeded = False
                response_text = "Git context 확인에 실패했습니다. 잠시 후 다시 요청해주세요."

        safe_text = prepare_output_markdown(response_text, max_chars=CHAT_REPLY_MAX_CHARS)
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
            logger.warning("Failed to post Slack Git context response: %s", error)
            await self._replace_in_progress_reaction(request, name=FAILURE_REACTION_NAME)

    async def _post_repo_catalog_response(self, request: SubmitSlackRequestInput) -> None:
        succeeded = True
        if self._git_context_provider is None:
            catalog = GitRepoCatalog(
                items=tuple(GitRepoCatalogItem(name=repo_key, status="ready") for repo_key in self._allowed_repo_keys),
                git_mcp_enabled=False,
            )
        else:
            try:
                catalog = await self._git_context_provider.fetch_repo_catalog(local_repo_keys=self._allowed_repo_keys)
            except GitContextDisabledError:
                catalog = GitRepoCatalog(
                    items=tuple(
                        GitRepoCatalogItem(name=repo_key, status="ready") for repo_key in self._allowed_repo_keys
                    ),
                    git_mcp_enabled=False,
                )
            except Exception as error:
                logger.warning("Failed to fetch repo catalog: %s", error)
                succeeded = False
                catalog = GitRepoCatalog(
                    items=tuple(
                        GitRepoCatalogItem(name=repo_key, status="ready") for repo_key in self._allowed_repo_keys
                    ),
                    git_mcp_enabled=False,
                )

        safe_text = prepare_output_markdown(format_repo_catalog_response(catalog), max_chars=CHAT_REPLY_MAX_CHARS)
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
            logger.warning("Failed to post Slack repo catalog response: %s", error)
            await self._replace_in_progress_reaction(request, name=FAILURE_REACTION_NAME)
