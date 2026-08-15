"""Secret-safe CLI output rendering."""

from __future__ import annotations

import json
from typing import cast

from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)

_REDACTOR = RedactionService(core_secret_redaction_policy())


def redact_text(value: str) -> str:
    """Remove common credential forms without exposing the matched value."""

    return cast(str, _REDACTOR.redact_text(value).value)


def redact_data(value: object, *, key: str | None = None) -> object:
    """Recursively redact values before text or JSON serialization."""

    if key is None:
        return _REDACTOR.redact_data(value).value
    wrapped = cast(dict[str, object], _REDACTOR.redact_data({key: value}).value)
    return next(iter(wrapped.values()))


def render_json(value: object) -> str:
    """Serialize only after recursive redaction."""

    return json.dumps(redact_data(value), ensure_ascii=False, sort_keys=True)
