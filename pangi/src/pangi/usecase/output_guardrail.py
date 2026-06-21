from __future__ import annotations

import re

from pangi.domain.policies import redact_secrets, truncate_text


DEFAULT_OUTPUT_MAX_CHARS = 3500
DEFAULT_EMPTY_OUTPUT = "팡이 응답이 비어 있습니다."

BROADCAST_MENTION_PATTERN = re.compile(r"(?<![`@\w])@(channel|here|everyone)\b")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def prepare_output_markdown(
    text: str | None,
    *,
    max_chars: int = DEFAULT_OUTPUT_MAX_CHARS,
    empty_fallback: str = DEFAULT_EMPTY_OUTPUT,
) -> str:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    safe_text = redact_secrets(text)
    safe_text = _normalize_line_endings(safe_text)
    safe_text = CONTROL_CHARACTER_PATTERN.sub("", safe_text)
    safe_text = _neutralize_broadcast_mentions(safe_text).strip()

    if not safe_text:
        safe_text = empty_fallback

    return truncate_text(safe_text, max_chars=max_chars)


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _neutralize_broadcast_mentions(text: str) -> str:
    return BROADCAST_MENTION_PATTERN.sub(r"`@\1`", text)
