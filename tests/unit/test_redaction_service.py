"""Versioned central redaction contracts and recursive behavior."""

from dataclasses import replace

import pytest

from pangi.application.contracts.redaction import (
    RedactionErrorCode,
    RedactionInputError,
    RedactionPolicy,
)
from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)


def test_core_policy_has_a_stable_canonical_fingerprint() -> None:
    first = core_secret_redaction_policy()
    second = core_secret_redaction_policy()

    assert first == second
    assert first.policy_version == "core-secret-v1"
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    with pytest.raises(ValueError, match="unique"):
        RedactionPolicy(
            policy_version="duplicate-v1",
            rules=(first.rules[0], first.rules[0]),
            max_depth=10,
            max_collection_items=100,
        )


def test_text_redaction_reports_rules_without_retaining_secret_values() -> None:
    raw_secrets = (
        "private-token-value",
        "private-password",
        "sk-abcdefghijk",
        "xoxb-private-token",
        "slack/bot-token",
    )
    text = (
        "token=private-token-value password:private-password "
        "sk-abcdefghijk xoxb-private-token secret://slack/bot-token"
    )

    result = RedactionService(core_secret_redaction_policy()).redact_text(text)

    assert isinstance(result.value, str)
    assert result.summary.redaction_count == 5
    assert result.summary.applied_rule_ids == (
        "credential-assignment",
        "known-token-prefix",
        "secret-reference",
    )
    rendered = f"{result!r} {result.summary!r} {result.summary.as_dict()!r}"
    for secret in raw_secrets:
        assert secret not in str(result.value)
        assert secret not in rendered


def test_nested_data_uses_key_and_text_rules_without_mutating_input() -> None:
    source: dict[str, object] = {
        "api_key": "sk-abcdefgh",
        "nested": {
            "authorization_header": "Bearer private-value",
            "safe": "password=private-password",
            "secretary": "visible",
            "token_count": 7,
        },
        "items": ("secret://vault/private", "plain"),
    }

    result = RedactionService(core_secret_redaction_policy()).redact_data(source)

    assert result.value == {
        "api_key": "[REDACTED]",
        "nested": {
            "authorization_header": "[REDACTED]",
            "safe": "password=[REDACTED]",
            "secretary": "visible",
            "token_count": 7,
        },
        "items": ["secret://[REDACTED]", "plain"],
    }
    assert source["api_key"] == "sk-abcdefgh"
    assert result.summary.redaction_count == 4
    assert set(result.summary.applied_rule_ids) == {
        "credential-assignment",
        "secret-reference",
        "sensitive-key",
    }


def test_sensitive_key_wrapper_and_authorization_text_are_compatible() -> None:
    service = RedactionService(core_secret_redaction_policy())

    key_result = service.redact_data({"client_secret": "private", "safe": "visible"})
    text_result = service.redact_text("Authorization: Bearer private-value")

    assert key_result.value == {"client_secret": "[REDACTED]", "safe": "visible"}
    assert text_result.value == "authorization=[REDACTED]"


@pytest.mark.parametrize(
    ("policy", "value", "code"),
    (
        (
            replace(core_secret_redaction_policy(), max_depth=1),
            {"one": {"two": {"three": "value"}}},
            RedactionErrorCode.INPUT_TOO_DEEP,
        ),
        (
            replace(core_secret_redaction_policy(), max_collection_items=2),
            ["one", "two", "three"],
            RedactionErrorCode.INPUT_TOO_LARGE,
        ),
    ),
)
def test_recursive_limits_fail_with_safe_stable_errors(
    policy: RedactionPolicy,
    value: object,
    code: RedactionErrorCode,
) -> None:
    with pytest.raises(RedactionInputError) as captured:
        RedactionService(policy).redact_data(value)

    assert captured.value.code is code
    assert "one" not in repr(captured.value)


def test_recursive_cycle_is_rejected_without_rendering_source_data() -> None:
    cyclic: list[object] = ["private-value"]
    cyclic.append(cyclic)

    with pytest.raises(RedactionInputError) as captured:
        RedactionService(core_secret_redaction_policy()).redact_data(cyclic)

    assert captured.value.code is RedactionErrorCode.INPUT_CYCLE
    assert "private-value" not in str(captured.value)
    assert "private-value" not in repr(captured.value)
