"""Versioned telemetry normalization, redaction, and safe failure tests."""

from dataclasses import replace

import pytest

from pangi.application.contracts.telemetry import TelemetryRedactionError
from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)
from pangi.application.services.telemetry_redaction import (
    TelemetryRedactionService,
    core_telemetry_redaction_policy,
    core_telemetry_redaction_service,
)
from pangi.domain.telemetry import TelemetryRedactionErrorCode


def test_core_policy_is_explicit_deterministic_and_bounded() -> None:
    first = core_telemetry_redaction_policy()
    second = core_telemetry_redaction_policy()

    assert first == second
    assert first.policy_version == "core-telemetry-v1"
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    assert first.max_depth == 32
    assert first.max_collection_items == 10_000


def test_event_redaction_normalizes_text_and_never_retains_secret_values() -> None:
    secret = "event-private-value"

    safe = core_telemetry_redaction_service().sanitize_event(
        f"token={secret}\r\nCafe\u0301",
        {
            "api_key": secret,
            "nested": {"note": f"password={secret}\rline"},
        },
    )

    assert safe.message == "token=[REDACTED]\nCafé"
    assert safe.attributes == {
        "api_key": "[REDACTED]",
        "nested": {"note": "password=[REDACTED]\nline"},
    }
    assert safe.summary.changed
    assert safe.summary.normalized
    assert safe.summary.redaction.redaction_count == 3
    rendered = f"{safe!r} {safe.summary!r} {safe.summary.as_dict()!r}"
    assert secret not in rendered


def test_log_redaction_keeps_only_allowlisted_fields() -> None:
    secret = "log-private-value"

    safe = core_telemetry_redaction_service().sanitize_log(
        f"token={secret}\r\nready",
        {
            "request_id": "request_12345678",
            "error_code": f"password={secret}",
            "raw_prompt": secret,
            "arbitrary": secret,
        },
        exception_type="RuntimeError",
    )

    assert safe.message == "token=[REDACTED]\nready"
    assert safe.fields == {
        "request_id": "request_12345678",
        "error_code": "password=[REDACTED]",
    }
    assert safe.exception_type == "RuntimeError"
    assert safe.summary.dropped_field_count == 2
    assert secret not in repr(safe)

    spoofed = core_telemetry_redaction_service().sanitize_log(
        "safe",
        {"exception_type": "ForgedError"},
    )
    assert spoofed.fields == {}
    assert spoofed.exception_type is None


@pytest.mark.parametrize(
    ("attributes", "code"),
    (
        (
            {"raw-prompt": "private-value"},
            TelemetryRedactionErrorCode.FORBIDDEN_EVENT_FIELD,
        ),
        (
            {"api_key": "x" * 128},
            TelemetryRedactionErrorCode.EVENT_PAYLOAD_TOO_LARGE,
        ),
    ),
)
def test_event_rejections_use_stable_secret_safe_codes(
    attributes: dict[str, object],
    code: TelemetryRedactionErrorCode,
) -> None:
    policy = replace(core_telemetry_redaction_policy(), max_event_attributes_bytes=32)
    service = TelemetryRedactionService(
        policy,
        RedactionService(core_secret_redaction_policy()),
    )

    with pytest.raises(TelemetryRedactionError) as captured:
        service.sanitize_event(None, attributes)

    assert captured.value.code is code
    assert "private-value" not in repr(captured.value)


def test_cycles_fail_closed_without_rendering_source_values() -> None:
    secret = "cycle-private-value"
    cyclic: list[object] = [secret]
    cyclic.append(cyclic)

    with pytest.raises(TelemetryRedactionError) as captured:
        core_telemetry_redaction_service().sanitize_event(
            None,
            {"safe": cyclic},
        )

    assert captured.value.code is TelemetryRedactionErrorCode.REDACTION_FAILED
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)
