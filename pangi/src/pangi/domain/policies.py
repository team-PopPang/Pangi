from __future__ import annotations

import re


REDACTION_TEXT = "[REDACTED]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9_-]+"),
    re.compile(r"(?i)\b(SLACK_[A-Z0-9_]*|[A-Z0-9_]*TOKEN|[A-Z0-9_]*SECRET)\s*=\s*[^\s]+"),
)


def redact_secrets(text: str | None) -> str:
    if not text:
        return ""

    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(REDACTION_TEXT, redacted)
    return redacted


def truncate_text(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars].rstrip()}\n\n... {omitted} chars truncated ..."
