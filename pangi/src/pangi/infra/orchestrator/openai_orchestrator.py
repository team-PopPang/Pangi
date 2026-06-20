from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from pangi.config import get_settings
from pangi.usecase.classify_request import (
    ClassifiedRequest,
    RequestClassification,
    classify_request,
    normalize_orchestrator_decision,
)
from pangi.usecase.ports import RequestOrchestrator


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ORCHESTRATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "classification": {
            "type": "string",
            "enum": [classification.value for classification in RequestClassification],
        },
        "should_create_job": {"type": "boolean"},
        "repo_key": {"type": ["string", "null"]},
        "reply_text": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
    },
    "required": ["classification", "should_create_job", "repo_key", "reply_text", "reason"],
}


class DeterministicRequestOrchestrator:
    async def decide(self, *, text: str, allowed_repo_keys: tuple[str, ...]) -> ClassifiedRequest:
        return classify_request(text, allowed_repo_keys=allowed_repo_keys)


@dataclass(frozen=True)
class OpenAIRequestOrchestrator:
    api_key: str
    model: str
    reasoning_effort: str
    service_tier: str
    api_url: str = OPENAI_RESPONSES_URL

    async def decide(self, *, text: str, allowed_repo_keys: tuple[str, ...]) -> ClassifiedRequest:
        fallback = classify_request(text, allowed_repo_keys=allowed_repo_keys)
        if fallback.kind in {
            RequestClassification.BLOCKED_WEB_ANALYSIS,
            RequestClassification.UNSUPPORTED,
        }:
            return fallback

        raw_decision = await asyncio.to_thread(
            self._request_decision,
            text,
            allowed_repo_keys,
        )
        return normalize_orchestrator_decision(raw_decision, allowed_repo_keys=allowed_repo_keys)

    def _request_decision(self, text: str, allowed_repo_keys: tuple[str, ...]) -> ClassifiedRequest:
        body = json.dumps(self._payload(text, allowed_repo_keys)).encode("utf-8")
        req = request.Request(
            self.api_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI orchestrator request failed: {error.code} {detail}") from error
        except URLError as error:
            raise RuntimeError(f"OpenAI orchestrator request failed: {error.reason}") from error

        output_text = _extract_response_text(payload)
        try:
            data = json.loads(output_text)
        except json.JSONDecodeError as error:
            raise RuntimeError("OpenAI orchestrator returned non-JSON output") from error

        return ClassifiedRequest(
            kind=RequestClassification(data["classification"]),
            should_create_job=bool(data["should_create_job"]),
            repo_key=data.get("repo_key"),
            reply_text=data.get("reply_text"),
            reason=data.get("reason"),
        )

    def _payload(self, text: str, allowed_repo_keys: tuple[str, ...]) -> dict[str, Any]:
        repo_list = ", ".join(allowed_repo_keys) or "(none)"
        return {
            "model": self.model,
            "service_tier": self.service_tier,
            "reasoning": {"effort": self.reasoning_effort},
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "pangi_orchestrator_decision",
                    "strict": True,
                    "schema": ORCHESTRATOR_SCHEMA,
                },
            },
            "instructions": (
                "You are Pangi's request orchestrator for the PopPang Slack bot. "
                "Return only the structured JSON decision. "
                "Classify ordinary conversation, greetings, text cleanup, and general analysis as codex_chat. "
                "Classify external web, internet search, URL, news, article, blog, or arbitrary link analysis as blocked_web_analysis. "
                "Classify repo/code analysis with an explicit allowed repo key as repo_analysis. "
                "Classify repo/code analysis without a repo key as needs_repo. "
                "Classify code edits, PR creation, deploy, commit, push, and write operations as unsupported. "
                "Never create a repo job unless the repo_key is one of the allowed repo keys."
            ),
            "input": (
                f"Allowed repo keys: {repo_list}\n"
                f"Slack message:\n{text}"
            ),
            "max_output_tokens": 500,
        }


def _extract_response_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]

    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                return content["text"]

    raise RuntimeError("OpenAI orchestrator response did not contain output text")


_request_orchestrator: RequestOrchestrator | None = None


def get_request_orchestrator() -> RequestOrchestrator:
    global _request_orchestrator
    if _request_orchestrator is not None:
        return _request_orchestrator

    settings = get_settings()
    if settings.openai_api_key:
        _request_orchestrator = OpenAIRequestOrchestrator(
            api_key=settings.openai_api_key,
            model=settings.orchestrator_model,
            reasoning_effort=settings.orchestrator_reasoning_effort,
            service_tier=settings.orchestrator_service_tier,
        )
    else:
        _request_orchestrator = DeterministicRequestOrchestrator()
    return _request_orchestrator


def set_request_orchestrator(orchestrator: RequestOrchestrator | None) -> None:
    global _request_orchestrator
    _request_orchestrator = orchestrator
