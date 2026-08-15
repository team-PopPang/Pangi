"""Deterministic final Output sanitization and secret-safe rejection behavior."""

from dataclasses import replace

import pytest

from pangi.application.contracts.output_guardrails import (
    OutputCandidate,
    OutputGuardrailBlockedError,
    OutputGuardrailPolicy,
)
from pangi.application.services.output_guardrails import (
    OutputGuardrailService,
    core_output_internal_detail_rules,
)
from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)
from pangi.domain.guardrails import TrustLevel
from pangi.domain.output_guardrails import (
    OutputGuardrailErrorCode,
    OutputGuardrailOutcome,
    OutputGuardrailStage,
)


def _policy(**changes: object) -> OutputGuardrailPolicy:
    values: dict[str, object] = {
        "policy_version": "output-v1",
        "max_input_bytes": 4_096,
        "max_output_bytes": 1_024,
        "max_mentions": 2,
        "max_evidence_links": 2,
        "max_evidence_link_bytes": 2_048,
        "allowed_link_schemes": frozenset({"https"}),
        "allow_relative_links": True,
        "broadcast_mentions": frozenset({"@channel", "@everyone", "@here"}),
        "internal_detail_rules": core_output_internal_detail_rules(),
        "truncation_marker": "\n\n[OUTPUT TRUNCATED]",
    }
    values.update(changes)
    return OutputGuardrailPolicy(**values)  # type: ignore[arg-type]


def _service(policy: OutputGuardrailPolicy | None = None) -> OutputGuardrailService:
    return OutputGuardrailService(
        policy or _policy(),
        redactor=RedactionService(core_secret_redaction_policy()),
    )


def test_policy_fingerprint_is_canonical_and_limits_are_explicit() -> None:
    first = _policy(allowed_link_schemes=frozenset({"HTTPS"}))
    second = _policy(allowed_link_schemes=frozenset({"https"}))

    assert first.allowed_link_schemes == frozenset({"https"})
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    with pytest.raises(TypeError):
        OutputGuardrailPolicy()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="max_input_bytes"):
        _policy(max_input_bytes=0)
    with pytest.raises(ValueError, match="truncation_marker"):
        _policy(truncation_marker="@unsafe")
    with pytest.raises(ValueError, match="unsafe URI scheme"):
        _policy(allowed_link_schemes=frozenset({"https", "javascript"}))
    with pytest.raises(ValueError, match="truncation_marker"):
        _policy(truncation_marker="\n](javascript:alert)")


def test_safe_output_is_normalized_and_has_reproducible_metadata() -> None:
    candidate = OutputCandidate(
        markdown="Cafe\u0301\r\n안전한 답변",
        evidence_links=("https://example.com/e\u0301",),
    )

    first = _service().sanitize(candidate)
    second = _service().sanitize(candidate)

    assert first.markdown == "Café\n안전한 답변"
    assert first.evidence_links == ("https://example.com/é",)
    assert first == second
    assert first.decision.stage is OutputGuardrailStage.COMPLETE
    assert first.decision.outcome is OutputGuardrailOutcome.ALLOWED
    assert first.decision.trust_level is TrustLevel.UNTRUSTED
    assert first.summary.changed is False
    assert first.as_metadata()["evidence_link_count"] == 1


def test_central_redaction_covers_markdown_and_evidence_without_secret_metadata() -> None:
    secrets = ("private-token-value", "sk-abcdefghijk")
    candidate = OutputCandidate(
        markdown="token=private-token-value and sk-abcdefghijk",
        evidence_links=("https://example.com/?token=private-token-value",),
    )

    safe = _service().sanitize(candidate)

    rendered = f"{safe!r} {safe.summary!r} {safe.as_metadata()!r}"
    for secret in secrets:
        assert secret not in safe.markdown
        assert secret not in safe.evidence_links[0]
        assert secret not in rendered
    assert safe.summary.redaction.redaction_count == 3
    assert "credential-assignment" in safe.summary.redaction.applied_rule_ids
    assert "known-token-prefix" in safe.summary.redaction.applied_rule_ids


def test_python_traceback_and_unix_path_are_removed() -> None:
    candidate = OutputCandidate(
        markdown=(
            "작업 실패\n"
            "Traceback (most recent call last):\n"
            '  File "/Users/example/project/app.py", line 2, in run\n'
            "ValueError: private internal failure\n\n"
            "로그: /private/var/tmp/pangi/output.log"
        )
    )

    safe = _service().sanitize(candidate)

    assert "Traceback" not in safe.markdown
    assert "/Users/" not in safe.markdown
    assert "/private/" not in safe.markdown
    assert safe.markdown.count("[INTERNAL DETAIL REMOVED]") == 2
    assert safe.summary.internal_detail_count == 2
    assert safe.summary.applied_internal_rule_ids == (
        "python-traceback",
        "unix-internal-path",
    )


def test_node_stack_and_windows_path_are_removed() -> None:
    candidate = OutputCandidate(
        markdown=(
            "Error: failed\n"
            "    at handler (/Users/example/project/app.js:10:2)\n"
            "cache C:\\Users\\example\\Pangi\\cache.db"
        )
    )

    safe = _service().sanitize(candidate)

    assert "at handler" not in safe.markdown
    assert "C:\\Users" not in safe.markdown
    assert safe.summary.internal_detail_count == 2
    assert safe.summary.applied_internal_rule_ids == (
        "node-stack-frame",
        "windows-internal-path",
    )


def test_raw_html_and_slack_markup_are_escaped_idempotently() -> None:
    first = _service().sanitize(
        OutputCandidate(markdown='<script>alert(1)</script> <@U123> <https://example.com|label>')
    )
    second = _service().sanitize(
        OutputCandidate(markdown=first.markdown, evidence_links=first.evidence_links)
    )

    assert "<" not in first.markdown
    assert ">" not in first.markdown
    assert first.markdown == second.markdown
    assert first.summary.escaped_html_character_count == 8
    assert second.summary.escaped_html_character_count == 0


def test_inline_links_keep_allowed_targets_and_remove_unsafe_schemes() -> None:
    safe = _service().sanitize(
        OutputCandidate(
            markdown=(
                "[https](https://example.com) [relative](guide/setup.md) "
                "[js](javascript:alert(1)) [data](data:text/html,bad) "
                "[file](file:///etc/passwd) [network](//evil.example/path)"
            )
        )
    )

    assert "[https](https://example.com)" in safe.markdown
    assert "[relative](guide/setup.md)" in safe.markdown
    assert "javascript:" not in safe.markdown
    assert "data:text" not in safe.markdown
    assert "file:///" not in safe.markdown
    assert "//evil.example" not in safe.markdown
    assert "[js]" in safe.markdown
    assert safe.summary.removed_markdown_link_count == 4


def test_reference_links_count_only_removed_definitions() -> None:
    safe = _service().sanitize(
        OutputCandidate(
            markdown=(
                "[safe]: https://example.com \"title\"\n"
                "[relative]: docs/guide.md\n"
                "[bad]: javascript:alert(1)\n"
                "[file]: file:///private/result"
            )
        )
    )

    assert "[safe]: https://example.com" in safe.markdown
    assert "[relative]: docs/guide.md" in safe.markdown
    assert "[bad]:" not in safe.markdown
    assert "[file]:" not in safe.markdown
    assert safe.summary.removed_markdown_link_count == 2


def test_nested_unsafe_link_destination_is_removed_as_one_link() -> None:
    safe = _service().sanitize(OutputCandidate(markdown="[click](javascript:alert(1)) after"))

    assert safe.markdown == "[click] after"
    assert safe.summary.removed_markdown_link_count == 1


def test_evidence_links_share_scheme_count_and_byte_policies() -> None:
    service = _service(_policy(max_evidence_links=3, max_evidence_link_bytes=24))
    safe = service.sanitize(
        OutputCandidate(
            markdown="결과",
            evidence_links=(
                "https://ok.example/a",
                "javascript:alert(1)",
                "https://example.com/path-that-is-too-long",
                "https://ignored.example",
            ),
        )
    )

    assert safe.evidence_links == ("https://ok.example/a",)
    assert safe.summary.removed_evidence_link_count == 3


def test_broadcast_mentions_are_always_neutralized_and_other_mentions_are_capped() -> None:
    safe = _service().sanitize(
        OutputCandidate(
            markdown=(
                "@channel @here @alice @bob @carol @everyone "
                "contact@example.com"
            )
        )
    )

    assert "@channel" not in safe.markdown
    assert "@here" not in safe.markdown
    assert "@everyone" not in safe.markdown
    assert "@alice" in safe.markdown
    assert "@bob" in safe.markdown
    assert "＠carol" in safe.markdown
    assert "contact@example.com" in safe.markdown
    assert safe.summary.neutralized_mention_count == 4


def test_utf8_truncation_preserves_codepoints_and_is_output_idempotent() -> None:
    policy = _policy(max_output_bytes=40)
    first = _service(policy).sanitize(OutputCandidate(markdown="한글🙂" * 20))
    second = _service(policy).sanitize(OutputCandidate(markdown=first.markdown))

    assert len(first.markdown.encode("utf-8")) <= 40
    assert first.markdown.endswith("[OUTPUT TRUNCATED]")
    assert "�" not in first.markdown
    assert first.summary.truncated is True
    assert second.markdown == first.markdown
    assert second.summary.truncated is False


def test_input_limit_counts_evidence_and_blocks_with_safe_error() -> None:
    secret = "private-secret-payload"
    service = _service(_policy(max_input_bytes=32, max_output_bytes=31))

    with pytest.raises(OutputGuardrailBlockedError) as captured:
        service.sanitize(
            OutputCandidate(
                markdown="safe",
                evidence_links=(f"https://example.com/{secret}",),
            )
        )

    error = captured.value
    assert error.code is OutputGuardrailErrorCode.INPUT_BYTES_EXCEEDED
    assert error.decision.stage is OutputGuardrailStage.INPUT
    assert error.decision.outcome is OutputGuardrailOutcome.BLOCKED
    assert secret not in f"{error!s} {error!r} {error.decision!r}"


def test_output_that_becomes_empty_is_blocked() -> None:
    with pytest.raises(OutputGuardrailBlockedError) as captured:
        _service().sanitize(OutputCandidate(markdown="[bad]: javascript:alert(1)"))

    assert captured.value.code is OutputGuardrailErrorCode.EMPTY_OUTPUT
    assert captured.value.decision.stage is OutputGuardrailStage.OUTPUT


def test_candidate_safe_output_and_policy_repr_hide_raw_rules_and_content() -> None:
    secret = "private-output-value"
    candidate = OutputCandidate(
        markdown=f"token={secret}",
        evidence_links=(f"https://x/?token={secret}",),
    )
    policy = _policy()
    safe = _service(policy).sanitize(candidate)

    assert secret not in repr(candidate)
    assert secret not in repr(safe)
    assert core_output_internal_detail_rules()[0].pattern not in repr(policy)
    assert safe.markdown == "token=[REDACTED]"


def test_malformed_link_text_is_stable_and_never_becomes_more_permissive() -> None:
    first = _service().sanitize(
        OutputCandidate(markdown="broken [link](javascript:alert(1) and <tag")
    )
    second = _service().sanitize(OutputCandidate(markdown=first.markdown))

    assert "<" not in first.markdown
    assert "javascript:" in first.markdown
    assert first.markdown == second.markdown


def test_policy_change_produces_a_distinct_decision_fingerprint() -> None:
    first = _service(_policy(policy_version="output-v1")).sanitize(
        OutputCandidate(markdown="same")
    )
    second = _service(
        replace(_policy(policy_version="output-v2"), max_mentions=3)
    ).sanitize(OutputCandidate(markdown="same"))

    assert first.content_fingerprint == second.content_fingerprint
    assert first.decision.policy_fingerprint != second.decision.policy_fingerprint
