from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol
from urllib import request

from pangi.config import get_settings
from pangi.infra.slack.markdown_to_slack import markdown_to_slack


SLACK_API_BASE_URL = "https://slack.com/api"


class SlackClient(Protocol):
    """팡이가 사용하는 Slack Web API 기능에 대한 infra adapter 계약."""

    async def post_message(self, *, channel_id: str, text: str, thread_ts: str | None = None) -> str | None:
        """Slack `chat.postMessage`에 해당하는 메시지 전송 작업을 수행하고 ts를 반환한다."""
        ...

    async def add_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        """Slack `reactions.add`에 해당하는 reaction 추가 작업을 수행한다."""
        ...

    async def remove_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        """Slack `reactions.remove`에 해당하는 reaction 제거 작업을 수행한다."""
        ...


class SlackApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class SlackWebClient:
    bot_token: str
    api_base_url: str = SLACK_API_BASE_URL

    async def post_message(self, *, channel_id: str, text: str, thread_ts: str | None = None) -> str | None:
        payload: dict[str, object] = {
            "channel": channel_id,
            "text": markdown_to_slack(text),
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts
        data = await asyncio.to_thread(self._post_json, "/chat.postMessage", payload) or {}
        ts = data.get("ts")
        return str(ts) if ts else None

    async def add_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        payload: dict[str, object] = {
            "channel": channel_id,
            "timestamp": message_ts,
            "name": name,
        }
        await asyncio.to_thread(
            self._post_json,
            "/reactions.add",
            payload,
            {"already_reacted"},
        )

    async def remove_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        payload: dict[str, object] = {
            "channel": channel_id,
            "timestamp": message_ts,
            "name": name,
        }
        await asyncio.to_thread(
            self._post_json,
            "/reactions.remove",
            payload,
            {"no_reaction", "not_reacted"},
        )

    def _post_json(
        self,
        path: str,
        payload: dict[str, object],
        ignored_errors: set[str] | None = None,
    ) -> dict[str, object]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.api_base_url}{path}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=10) as response:
            response_body = response.read().decode("utf-8")
        data = json.loads(response_body)
        if not data.get("ok"):
            error = data.get("error") or "unknown_error"
            if ignored_errors and error in ignored_errors:
                return data
            raise SlackApiError(f"Slack API request failed: {error}")
        return data


_slack_client: SlackClient | None = None


def get_slack_client() -> SlackClient:
    global _slack_client
    if _slack_client is None:
        _slack_client = SlackWebClient(bot_token=get_settings().slack_bot_token)
    return _slack_client


def set_slack_client(client: SlackClient | None) -> None:
    global _slack_client
    _slack_client = client
