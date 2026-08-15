"""Final-handler logging redaction and fail-safe behavior."""

import io
import logging

from pangi.adapters.outbound.logging import (
    TelemetryRedactionFilter,
    install_telemetry_redaction_filter,
)
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)


def _logger(
    name: str,
    *,
    formatter: str = "%(message)s",
) -> tuple[logging.Logger, io.StringIO, logging.Handler]:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter(formatter))
    handler.addFilter(TelemetryRedactionFilter(core_telemetry_redaction_service()))
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, output, handler


def test_percent_arguments_and_extras_are_redacted_before_formatting() -> None:
    secret = "format-private-value"
    logger, output, _handler = _logger(
        "pangi.tests.telemetry.format",
        formatter="%(message)s|%(request_id)s",
    )

    logger.error(
        "token=%s",
        secret,
        extra={"request_id": "request_12345678", "raw_prompt": secret},
    )

    assert output.getvalue().strip() == "token=[REDACTED]|request_12345678"
    assert secret not in output.getvalue()


def test_exception_and_stack_payloads_are_removed_but_type_is_preserved() -> None:
    secret = "exception-private-value"
    logger, output, _handler = _logger(
        "pangi.tests.telemetry.exception",
        formatter="%(message)s|%(exception_type)s",
    )

    try:
        raise RuntimeError(f"password={secret}")
    except RuntimeError:
        logger.exception("authorization=Bearer %s", secret, stack_info=True)

    rendered = output.getvalue()
    assert rendered.strip() == "authorization=[REDACTED]|RuntimeError"
    assert secret not in rendered
    assert "Traceback" not in rendered
    assert "Stack" not in rendered


def test_filter_failure_emits_only_a_stable_fallback() -> None:
    secret = "fallback-private-value"
    logger, output, _handler = _logger("pangi.tests.telemetry.fallback")

    logger.error("value=%d", f"password={secret}")

    assert output.getvalue().strip() == "Log payload rejected by telemetry redaction"
    assert secret not in output.getvalue()


def test_installer_covers_existing_handlers_without_duplicates() -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler(io.StringIO())
    redaction_filter = TelemetryRedactionFilter(core_telemetry_redaction_service())
    root.addHandler(handler)
    try:
        first = install_telemetry_redaction_filter(redaction_filter)
        second = install_telemetry_redaction_filter(redaction_filter)

        assert handler in first
        assert handler in second
        assert handler.filters.count(redaction_filter) == 1
    finally:
        root.removeHandler(handler)
        handler.close()
