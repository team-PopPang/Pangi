"""CLI redaction tests."""

from pangi.adapters.inbound.output import redact_data, redact_text, render_json


def test_text_redaction_removes_common_credentials() -> None:
    text = "token=abc123 password:letmein sk-abcdefgh secret://slack/bot-token"

    redacted = redact_text(text)

    assert "abc123" not in redacted
    assert "letmein" not in redacted
    assert "sk-abcdefgh" not in redacted
    assert "bot-token" not in redacted


def test_json_redaction_happens_before_serialization() -> None:
    output = render_json(
        {
            "api_key": "sk-abcdefgh",
            "nested": {"authorization": "Bearer private", "safe": "visible"},
        }
    )

    assert "sk-abcdefgh" not in output
    assert "Bearer private" not in output
    assert "visible" in output


def test_cli_redaction_delegates_nested_values_without_hiding_safe_metrics() -> None:
    redacted = redact_data(
        {
            "client_secret": "private",
            "token_count": 3,
            "safe": "Authorization: Bearer private-token",
        }
    )

    assert redacted == {
        "client_secret": "[REDACTED]",
        "token_count": 3,
        "safe": "authorization=[REDACTED]",
    }
