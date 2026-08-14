"""Secret-safe CLI output rendering."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

_SENSITIVE_KEY = re.compile(r"(?i)(token|secret|password|api[-_]?key|authorization)")
_ASSIGNMENT = re.compile(
    r"(?i)\b(token|secret|password|api[-_]?key|authorization)\b\s*[:=]\s*[^\s,}]+"
)
_CREDENTIAL = re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]+)\b")
_SECRET_REFERENCE = re.compile(r"secret://[^\s\"']+")


def redact_text(value: str) -> str:
    """Remove common credential forms without exposing the matched value."""

    redacted = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    redacted = _CREDENTIAL.sub("[REDACTED]", redacted)
    return _SECRET_REFERENCE.sub("secret://[REDACTED]", redacted)


def redact_data(value: object, *, key: str | None = None) -> object:
    """Recursively redact values before text or JSON serialization."""

    if key is not None and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_data(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_data(item) for item in value]
    return value


def render_json(value: object) -> str:
    """Serialize only after recursive redaction."""

    return json.dumps(redact_data(value), ensure_ascii=False, sort_keys=True)
