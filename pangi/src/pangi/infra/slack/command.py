from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


MENTION_PATTERN = re.compile(r"<@[A-Z0-9]+>")


@dataclass(frozen=True)
class SlackCommand:
    team_id: str
    channel_id: str
    user_id: str
    text: str
    thread_ts: str
    event_id: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def clean_mention_text(text: str) -> str:
    return MENTION_PATTERN.sub("", text or "").strip()


def command_from_app_mention(payload: dict[str, Any]) -> SlackCommand | None:
    event = payload.get("event") or {}
    if event.get("type") != "app_mention":
        return None
    if event.get("bot_id") or event.get("subtype"):
        return None

    channel_id = event.get("channel") or ""
    user_id = event.get("user") or ""
    thread_ts = event.get("thread_ts") or event.get("ts") or ""
    event_id = payload.get("event_id") or ""
    if not channel_id or not user_id or not thread_ts or not event_id:
        return None

    return SlackCommand(
        team_id=payload.get("team_id") or "",
        channel_id=channel_id,
        user_id=user_id,
        text=clean_mention_text(event.get("text") or ""),
        thread_ts=thread_ts,
        event_id=event_id,
    )


def command_from_slash_payload(form: dict[str, str]) -> SlackCommand | None:
    team_id = form.get("team_id") or ""
    channel_id = form.get("channel_id") or ""
    user_id = form.get("user_id") or ""
    trigger_id = form.get("trigger_id") or ""
    if not channel_id or not user_id:
        return None

    return SlackCommand(
        team_id=team_id,
        channel_id=channel_id,
        user_id=user_id,
        text=(form.get("text") or "").strip(),
        thread_ts=form.get("thread_ts") or form.get("message_ts") or "",
        event_id=trigger_id,
    )
