"""Framework-free telemetry redaction errors and Run Event restrictions."""

from enum import StrEnum

RUN_EVENT_FORBIDDEN_ATTRIBUTE_KEYS = frozenset(
    {
        "attachment_body",
        "chain_of_thought",
        "model_input",
        "model_output",
        "prompt",
        "provider_prompt",
        "raw_prompt",
        "secret",
        "slack_event",
        "tool_result",
    }
)


class TelemetryRedactionErrorCode(StrEnum):
    LOG_PAYLOAD_TOO_LARGE = "telemetry_log_payload_too_large"
    EVENT_PAYLOAD_TOO_LARGE = "telemetry_event_payload_too_large"
    FORBIDDEN_EVENT_FIELD = "telemetry_forbidden_event_field"
    REDACTION_FAILED = "telemetry_redaction_failed"
