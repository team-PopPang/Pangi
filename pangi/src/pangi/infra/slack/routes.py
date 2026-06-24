from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from time import monotonic
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from pangi.config import AccessDeniedError, get_settings
from pangi.infra.codex import get_chat_responder
from pangi.infra.git_mcp import get_git_context_provider
from pangi.infra.notion import get_notion_context_provider
from pangi.infra.orchestrator import get_request_orchestrator
from pangi.infra.queue import get_job_queue
from pangi.infra.slack.client import get_slack_client
from pangi.infra.slack.command import command_from_app_mention, command_from_slash_payload
from pangi.infra.slack.signature import verify_slack_signature
from pangi.repository import DuplicateEventError, get_job_repository
from pangi.usecase.git_context import (
    GitContextDisabledError,
    GitRepoCatalog,
    GitRepoCatalogItem,
    format_repo_catalog_response,
)
from pangi.usecase.input_guardrail import route_request_input
from pangi.usecase.request_decision import (
    GIT_CONTEXT_DISABLED_MESSAGE,
    NOTION_CONTEXT_DISABLED_MESSAGE,
    RequestClassification,
    build_needs_repo_message,
)
from pangi.usecase.submit_slack_request import SubmitSlackRequestInput, SubmitSlackRequestUseCase


router = APIRouter(prefix="/slack", tags=["slack"])
_processed_event_ids: set[str] = set()
logger = logging.getLogger(__name__)
GIT_REPO_KEYS_CACHE_TTL_SECONDS = 300
GIT_REPO_KEYS_TIMEOUT_SECONDS = 5
_allowed_repo_keys_cache: tuple[tuple[str, ...], float] | None = None
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


def reset_processed_event_ids() -> None:
    _processed_event_ids.clear()
    global _allowed_repo_keys_cache
    _allowed_repo_keys_cache = None


async def _verified_body(request: Request) -> bytes:
    body = await request.body()
    settings = get_settings()
    is_valid = verify_slack_signature(
        signing_secret=settings.slack_signing_secret,
        timestamp=request.headers.get("X-Slack-Request-Timestamp"),
        signature=request.headers.get("X-Slack-Signature"),
        body=body,
    )
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid Slack signature")
    return body


def _parse_urlencoded_body(body: bytes) -> dict[str, str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}


def _validate_access(*, user_id: str, channel_id: str) -> None:
    try:
        get_settings().validate_slack_access(user_id=user_id, channel_id=channel_id)
    except AccessDeniedError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


async def _allowed_repo_keys() -> tuple[str, ...]:
    global _allowed_repo_keys_cache
    local_repo_keys = get_settings().available_repo_keys()
    provider = get_git_context_provider()
    if provider is None:
        return local_repo_keys

    now = monotonic()
    if _allowed_repo_keys_cache is not None:
        keys, expires_at = _allowed_repo_keys_cache
        if now < expires_at:
            return keys

    try:
        catalog = await asyncio.wait_for(
            provider.fetch_repo_catalog(local_repo_keys=local_repo_keys),
            timeout=GIT_REPO_KEYS_TIMEOUT_SECONDS,
        )
    except Exception as error:
        logger.warning("Failed to fetch Git MCP repo keys: %s", error)
        return local_repo_keys
    keys = tuple(item.name for item in catalog.items)
    _allowed_repo_keys_cache = (keys, now + GIT_REPO_KEYS_CACHE_TTL_SECONDS)
    return keys


async def _allowed_repo_keys_for_text(text: str) -> tuple[str, ...]:
    local_repo_keys = get_settings().available_repo_keys()
    route = route_request_input(text, allowed_repo_keys=local_repo_keys)
    if (
        route.decision is not None
        and not route.needs_ai_orchestrator
        and route.decision.kind in _GIT_REPO_KEYS_OPTIONAL_CLASSIFICATIONS
    ):
        return local_repo_keys
    return await _allowed_repo_keys()


@router.post("/events")
async def slack_events(request: Request) -> JSONResponse:
    body = await _verified_body(request)

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None

    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge", "")})

    if payload.get("type") != "event_callback":
        return JSONResponse({"ok": True})

    event_id = payload.get("event_id") or ""
    repository = get_job_repository()
    existing_job = repository.find_job_by_event_id(event_id) if event_id else None
    if request.headers.get("X-Slack-Retry-Num") and event_id in _processed_event_ids:
        return JSONResponse({"ok": True, "duplicate": True, "job_id": existing_job.id if existing_job else ""})
    if request.headers.get("X-Slack-Retry-Num") and existing_job is not None:
        return JSONResponse({"ok": True, "duplicate": True, "job_id": existing_job.id})

    command = command_from_app_mention(payload)
    if command is None:
        return JSONResponse({"ok": True})

    _validate_access(user_id=command.user_id, channel_id=command.channel_id)
    event = payload.get("event") or {}
    request_input = SubmitSlackRequestInput(
        team_id=command.team_id,
        channel_id=command.channel_id,
        user_id=command.user_id,
        text=command.text,
        thread_ts=command.thread_ts,
        event_id=command.event_id,
        message_ts=event.get("ts") or command.thread_ts,
    )
    _processed_event_ids.add(command.event_id)
    return JSONResponse(
        {
            "ok": True,
            "command": command.to_dict(),
            "accepted": True,
        },
        background=BackgroundTask(_process_app_mention, request_input),
    )


@router.post("/commands")
async def slack_commands(request: Request) -> JSONResponse:
    body = await _verified_body(request)
    form = _parse_urlencoded_body(body)

    if form.get("ssl_check") == "1":
        return JSONResponse({"ok": True})

    command = command_from_slash_payload(form)
    if command is None:
        raise HTTPException(status_code=400, detail="Invalid Slack command payload")

    _validate_access(user_id=command.user_id, channel_id=command.channel_id)
    local_repo_keys = get_settings().available_repo_keys()
    allowed_repo_keys = await _allowed_repo_keys_for_text(command.text)
    orchestrator = get_request_orchestrator()
    decision = await orchestrator.decide(text=command.text, allowed_repo_keys=allowed_repo_keys)
    if not decision.should_create_job:
        if decision.kind == RequestClassification.REPO_CATALOG:
            text = await _repo_catalog_text(local_repo_keys=local_repo_keys)
        else:
            text = _reply_text_for_slash_decision(decision, allowed_repo_keys=allowed_repo_keys)
        return JSONResponse(
            {
                "ok": True,
                "response_type": "ephemeral",
                "text": text,
                "command": command.to_dict(),
                "classification": decision.kind.value,
            }
        )
    if decision.kind != RequestClassification.REPO_ANALYSIS or decision.repo_key is None:
        return JSONResponse(
            {
                "ok": True,
                "response_type": "ephemeral",
                "text": build_needs_repo_message(allowed_repo_keys),
                "command": command.to_dict(),
                "classification": RequestClassification.NEEDS_REPO.value,
            }
        )

    repository = get_job_repository()
    thread_ts = command.thread_ts or command.event_id or "slash_command"
    thread = repository.get_or_create_thread(
        team_id=command.team_id,
        channel_id=command.channel_id,
        thread_ts=thread_ts,
    )
    try:
        job = repository.create_job(
            event_id=command.event_id or f"slash:{command.channel_id}:{command.user_id}:{thread_ts}",
            slack_thread=thread,
            codex_session_id=thread.active_codex_session_id,
            requester_user_id=command.user_id,
            prompt=command.text,
            repo_key=decision.repo_key,
        )
    except DuplicateEventError:
        job = repository.find_job_by_event_id(command.event_id)
        return JSONResponse({"ok": True, "duplicate": True, "job_id": job.id if job else ""})

    await get_job_queue().enqueue(job.id)
    return JSONResponse(
        {
            "ok": True,
            "response_type": "ephemeral",
            "text": f"팡이가 요청을 접수했습니다. job_id: {job.id}",
            "command": command.to_dict(),
            "job_id": job.id,
            "job_status": job.status.value,
        }
    )


@router.post("/interactions")
async def slack_interactions(request: Request) -> JSONResponse:
    await _verified_body(request)
    return JSONResponse({"ok": False, "detail": "Slack interactions are not implemented yet"}, status_code=501)


async def _process_app_mention(request_input: SubmitSlackRequestInput) -> None:
    request_input = await _add_initial_in_progress_reaction(request_input)
    use_case = SubmitSlackRequestUseCase(
        repository=get_job_repository(),
        job_queue=get_job_queue(),
        slack_notifier=get_slack_client(),
        request_orchestrator=get_request_orchestrator(),
        chat_responder=get_chat_responder(),
        notion_context_provider=get_notion_context_provider(),
        git_context_provider=get_git_context_provider(),
        allowed_repo_keys=await _allowed_repo_keys_for_text(request_input.text),
        local_repo_keys=get_settings().available_repo_keys(),
    )
    try:
        await use_case.execute(request_input)
    except Exception as error:
        logger.exception("Failed to process Slack app mention %s: %s", request_input.event_id, error)


async def _add_initial_in_progress_reaction(request_input: SubmitSlackRequestInput) -> SubmitSlackRequestInput:
    if not request_input.message_ts:
        return request_input
    try:
        await get_slack_client().add_reaction(
            channel_id=request_input.channel_id,
            message_ts=request_input.message_ts,
            name="eyes",
        )
        return replace(request_input, reaction_already_added=True)
    except Exception as error:
        logger.warning("Failed to add early Slack reaction: %s", error)
        return request_input


async def _repo_catalog_text(*, local_repo_keys: tuple[str, ...]) -> str:
    provider = get_git_context_provider()
    if provider is None:
        return format_repo_catalog_response(_local_repo_catalog(local_repo_keys))
    try:
        catalog = await provider.fetch_repo_catalog(local_repo_keys=local_repo_keys)
    except GitContextDisabledError:
        catalog = _local_repo_catalog(local_repo_keys)
    except Exception as error:
        logger.warning("Failed to fetch repo catalog for slash command: %s", error)
        catalog = _local_repo_catalog(local_repo_keys)
    return format_repo_catalog_response(catalog)


def _local_repo_catalog(local_repo_keys: tuple[str, ...]) -> GitRepoCatalog:
    return GitRepoCatalog(
        items=tuple(GitRepoCatalogItem(name=repo_key, status="ready") for repo_key in local_repo_keys),
        git_mcp_enabled=False,
    )


def _reply_text_for_slash_decision(decision, *, allowed_repo_keys: tuple[str, ...]) -> str:
    if decision.reply_text:
        return decision.reply_text
    if decision.kind == RequestClassification.NEEDS_REPO:
        return build_needs_repo_message(allowed_repo_keys)
    if decision.kind == RequestClassification.NOTION_CONTEXT_CHAT:
        return NOTION_CONTEXT_DISABLED_MESSAGE
    if decision.kind in {RequestClassification.GIT_CONTEXT_CHAT, RequestClassification.REPO_CATALOG}:
        return GIT_CONTEXT_DISABLED_MESSAGE
    return "팡이는 이 요청을 repo 분석 job으로 실행하지 않았습니다."
