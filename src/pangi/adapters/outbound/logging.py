"""Standard-library logging redaction at the final handler boundary."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from pangi.application.services.telemetry_redaction import TelemetryRedactionService

_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
_FALLBACK_MESSAGE = "Log payload rejected by telemetry redaction"


class TelemetryRedactionFilter(logging.Filter):
    """Render once, redact, and remove raw exception or unapproved extra values."""

    def __init__(self, redactor: TelemetryRedactionService) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        custom_fields = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_FIELDS
        }
        exception_type = (
            record.exc_info[0].__name__
            if record.exc_info is not None and record.exc_info[0] is not None
            else None
        )
        try:
            safe = self._redactor.sanitize_log(
                record.getMessage(),
                custom_fields,
                exception_type=exception_type,
            )
            message = safe.message
            fields = dict(safe.fields)
            if safe.exception_type is not None:
                fields["exception_type"] = safe.exception_type
        except Exception:
            message = _FALLBACK_MESSAGE
            fields = {"error_code": "telemetry_redaction_failed"}

        record.msg = message
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        for key in custom_fields:
            record.__dict__.pop(key, None)
        record.__dict__.update(fields)
        return True


def install_telemetry_redaction_filter(
    redaction_filter: logging.Filter,
) -> tuple[logging.Handler, ...]:
    """Attach to handlers configured by Uvicorn plus the fallback handler."""

    handlers: list[logging.Handler] = []
    handlers.extend(logging.getLogger().handlers)
    for logger in _configured_loggers():
        handlers.extend(logger.handlers)
    if logging.lastResort is not None:
        handlers.append(logging.lastResort)

    installed: list[logging.Handler] = []
    seen: set[int] = set()
    for handler in handlers:
        if id(handler) in seen:
            continue
        seen.add(id(handler))
        if redaction_filter not in handler.filters:
            handler.addFilter(redaction_filter)
        installed.append(handler)
    return tuple(installed)


def _configured_loggers() -> Iterable[logging.Logger]:
    for candidate in logging.root.manager.loggerDict.values():
        if isinstance(candidate, logging.Logger):
            yield candidate
