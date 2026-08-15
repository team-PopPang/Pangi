"""Deterministic final Output normalization, redaction, and sanitization."""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import NoReturn, cast
from urllib.parse import urlsplit

from pangi.application.contracts.output_guardrails import (
    OutputCandidate,
    OutputGuardrailBlockedError,
    OutputGuardrailDecision,
    OutputGuardrailPolicy,
    OutputGuardrailSummary,
    OutputSanitizationRule,
    SafeOutput,
)
from pangi.application.services.redaction import RedactionService
from pangi.domain.output_guardrails import (
    OutputGuardrailErrorCode,
    OutputGuardrailOutcome,
    OutputGuardrailStage,
)

_INTERNAL_DETAIL = "[INTERNAL DETAIL REMOVED]"
_FULLWIDTH_AT = "＠"
_MENTION = re.compile(r"(?<![\w@])@[\w][\w.-]{0,79}", re.UNICODE)
_REFERENCE_LINK = re.compile(
    r"(?m)^(?P<prefix>[ \t]{0,3}\[[^\]\n]+\]:[ \t]*)(?P<target>\S+)(?P<rest>[^\n]*)$"
)


def core_output_internal_detail_rules() -> tuple[OutputSanitizationRule, ...]:
    """Return versioned rules for common stack traces and local absolute paths."""

    return (
        OutputSanitizationRule(
            rule_id="python-traceback",
            pattern=(
                r"^Traceback \(most recent call last\):\n"
                r".*?(?=\n[ \t]*\n|\Z)"
            ),
            replacement=_INTERNAL_DETAIL,
            flags=int(re.MULTILINE | re.DOTALL),
        ),
        OutputSanitizationRule(
            rule_id="node-stack-frame",
            pattern=r"(?m)^[ \t]*at[ \t]+[^\n]*(?:[/\\]|\()[^\n]*:\d+(?::\d+)?\)?[ \t]*$",
            replacement=_INTERNAL_DETAIL,
        ),
        OutputSanitizationRule(
            rule_id="unix-internal-path",
            pattern=(
                r"(?<![A-Za-z0-9:])/(?:Users|home|private|var|tmp|opt)/"
                r"[^\s<>()\[\]{}\"']+"
            ),
            replacement=_INTERNAL_DETAIL,
        ),
        OutputSanitizationRule(
            rule_id="windows-internal-path",
            pattern=(
                r"\b[A-Za-z]:\\(?:[^\\\s<>()\[\]{}\"']+\\)*"
                r"[^\\\s<>()\[\]{}\"']+"
            ),
            replacement=_INTERNAL_DETAIL,
            flags=int(re.IGNORECASE),
        ),
    )


@dataclass(frozen=True, slots=True)
class _CompiledRule:
    contract: OutputSanitizationRule
    pattern: re.Pattern[str]


class OutputGuardrailService:
    """Create a safe, reproducible Output without reinterpreting its meaning."""

    def __init__(
        self,
        policy: OutputGuardrailPolicy,
        *,
        redactor: RedactionService,
    ) -> None:
        self._policy = policy
        self._redactor = redactor
        self._internal_rules = tuple(
            _CompiledRule(rule, re.compile(rule.pattern, rule.flags))
            for rule in policy.internal_detail_rules
        )
        mention_pattern = "|".join(
            re.escape(mention)
            for mention in sorted(policy.broadcast_mentions, key=len, reverse=True)
        )
        self._broadcast_mention = re.compile(
            rf"(?<![\w@])(?:{mention_pattern})(?![\w.-])",
            re.IGNORECASE,
        )

    def sanitize(self, candidate: OutputCandidate) -> SafeOutput:
        markdown = unicodedata.normalize(
            "NFC",
            candidate.markdown.replace("\r\n", "\n").replace("\r", "\n"),
        )
        normalized_links = tuple(
            unicodedata.normalize("NFC", link.replace("\r\n", "\n").replace("\r", "\n"))
            for link in candidate.evidence_links
        )
        input_bytes = len(markdown.encode("utf-8")) + sum(
            len(link.encode("utf-8")) for link in normalized_links
        )
        if input_bytes > self._policy.max_input_bytes:
            self._block(
                OutputGuardrailStage.INPUT,
                OutputGuardrailErrorCode.INPUT_BYTES_EXCEEDED,
                input_bytes=input_bytes,
            )

        capped_links = normalized_links[: self._policy.max_evidence_links]
        removed_evidence_links = len(normalized_links) - len(capped_links)
        redacted = self._redactor.redact_data(
            {
                "markdown": markdown,
                "evidence_links": capped_links,
            }
        )
        redacted_value = cast(Mapping[str, object], redacted.value)
        safe_markdown = cast(str, redacted_value["markdown"])
        evidence_value = redacted_value["evidence_links"]
        if not isinstance(evidence_value, Sequence) or isinstance(evidence_value, str):
            raise RuntimeError("Redaction service returned invalid Output evidence")
        redacted_links = tuple(cast(str, link) for link in evidence_value)

        safe_markdown, internal_count, internal_rule_ids = self._remove_internal_details(
            safe_markdown
        )
        safe_markdown, escaped_html_characters = self._escape_raw_html(safe_markdown)
        safe_markdown, removed_markdown_links = self._sanitize_markdown_links(safe_markdown)
        safe_links, invalid_evidence_links = self._sanitize_evidence_links(redacted_links)
        removed_evidence_links += invalid_evidence_links
        safe_markdown, neutralized_mentions = self._sanitize_mentions(safe_markdown)
        safe_markdown, truncated = self._truncate(safe_markdown)
        if not safe_markdown.strip():
            self._block(
                OutputGuardrailStage.OUTPUT,
                OutputGuardrailErrorCode.EMPTY_OUTPUT,
                input_bytes=input_bytes,
            )

        output_bytes = len(safe_markdown.encode("utf-8"))
        decision = OutputGuardrailDecision(
            stage=OutputGuardrailStage.COMPLETE,
            outcome=OutputGuardrailOutcome.ALLOWED,
            policy_version=self._policy.policy_version,
            policy_fingerprint=self._policy.fingerprint,
            input_bytes=input_bytes,
        )
        summary = OutputGuardrailSummary(
            redaction=redacted.summary,
            output_bytes=output_bytes,
            internal_detail_count=internal_count,
            applied_internal_rule_ids=internal_rule_ids,
            escaped_html_character_count=escaped_html_characters,
            removed_markdown_link_count=removed_markdown_links,
            removed_evidence_link_count=removed_evidence_links,
            neutralized_mention_count=neutralized_mentions,
            truncated=truncated,
        )
        fingerprint_payload = json.dumps(
            {
                "evidence_links": list(safe_links),
                "markdown": safe_markdown,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return SafeOutput(
            markdown=safe_markdown,
            evidence_links=safe_links,
            content_fingerprint=hashlib.sha256(fingerprint_payload).hexdigest(),
            decision=decision,
            summary=summary,
        )

    def _remove_internal_details(self, value: str) -> tuple[str, int, tuple[str, ...]]:
        safe = value
        count = 0
        applied: list[str] = []
        for rule in self._internal_rules:
            safe, rule_count = rule.pattern.subn(rule.contract.replacement, safe)
            count += rule_count
            if rule_count:
                applied.append(rule.contract.rule_id)
        return safe, count, tuple(applied)

    @staticmethod
    def _escape_raw_html(value: str) -> tuple[str, int]:
        count = value.count("<") + value.count(">")
        return value.replace("<", "&lt;").replace(">", "&gt;"), count

    def _sanitize_markdown_links(self, value: str) -> tuple[str, int]:
        removed = 0

        def sanitize_reference(match: re.Match[str]) -> str:
            nonlocal removed
            target = _markdown_link_target(match.group("target"))
            if target is not None and self._is_allowed_link(target):
                return match.group(0)
            removed += 1
            return ""

        safe_references = _REFERENCE_LINK.sub(sanitize_reference, value)
        output: list[str] = []
        cursor = 0
        while cursor < len(safe_references):
            if (
                safe_references[cursor] == "]"
                and cursor + 1 < len(safe_references)
                and safe_references[cursor + 1] == "("
                and not _is_escaped(safe_references, cursor)
            ):
                end = _find_link_destination_end(safe_references, cursor + 2)
                if end is not None:
                    destination = safe_references[cursor + 2 : end]
                    target = _markdown_link_target(destination)
                    if target is None or not self._is_allowed_link(target):
                        output.append("]")
                        cursor = end + 1
                        removed += 1
                        continue
                    output.append(safe_references[cursor : end + 1])
                    cursor = end + 1
                    continue
            output.append(safe_references[cursor])
            cursor += 1
        return "".join(output), removed

    def _sanitize_evidence_links(self, links: tuple[str, ...]) -> tuple[tuple[str, ...], int]:
        safe: list[str] = []
        removed = 0
        for link in links:
            normalized = link.strip()
            if (
                not normalized
                or len(normalized.encode("utf-8")) > self._policy.max_evidence_link_bytes
                or not self._is_allowed_link(normalized)
            ):
                removed += 1
                continue
            safe.append(normalized)
        return tuple(safe), removed

    def _is_allowed_link(self, value: str) -> bool:
        normalized = html.unescape(value).strip()
        if normalized.startswith("<") and normalized.endswith(">"):
            normalized = normalized[1:-1].strip()
        if not normalized or any(
            character.isspace() or unicodedata.category(character) == "Cc"
            for character in normalized
        ):
            return False
        inspection_value = normalized.replace("\\", "")
        try:
            parsed = urlsplit(inspection_value)
        except ValueError:
            return False
        if parsed.scheme:
            return parsed.scheme.casefold() in self._policy.allowed_link_schemes
        if parsed.netloc or inspection_value.startswith("//"):
            return False
        return self._policy.allow_relative_links

    def _sanitize_mentions(self, value: str) -> tuple[str, int]:
        safe, broadcast_count = self._broadcast_mention.subn(
            lambda match: _FULLWIDTH_AT + match.group(0)[1:],
            value,
        )
        seen = 0
        excess_count = 0

        def replace_excess(match: re.Match[str]) -> str:
            nonlocal seen, excess_count
            seen += 1
            if seen <= self._policy.max_mentions:
                return match.group(0)
            excess_count += 1
            return _FULLWIDTH_AT + match.group(0)[1:]

        return _MENTION.sub(replace_excess, safe), broadcast_count + excess_count

    def _truncate(self, value: str) -> tuple[str, bool]:
        encoded = value.encode("utf-8")
        if len(encoded) <= self._policy.max_output_bytes:
            return value, False
        marker = self._policy.truncation_marker
        marker_bytes = marker.encode("utf-8")
        prefix_limit = self._policy.max_output_bytes - len(marker_bytes)
        prefix = encoded[:prefix_limit].decode("utf-8", errors="ignore").rstrip()
        return prefix + marker, True

    def _block(
        self,
        stage: OutputGuardrailStage,
        error_code: OutputGuardrailErrorCode,
        *,
        input_bytes: int,
    ) -> NoReturn:
        raise OutputGuardrailBlockedError(
            OutputGuardrailDecision(
                stage=stage,
                outcome=OutputGuardrailOutcome.BLOCKED,
                policy_version=self._policy.policy_version,
                policy_fingerprint=self._policy.fingerprint,
                input_bytes=input_bytes,
                error_code=error_code,
            )
        )


def _is_escaped(value: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and value[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _find_link_destination_end(value: str, start: int) -> int | None:
    depth = 1
    cursor = start
    while cursor < len(value):
        character = value[cursor]
        if character == "\\":
            cursor += 2
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return None


def _markdown_link_target(value: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.startswith("&lt;"):
        closing = stripped.find("&gt;")
        if closing == -1:
            return None
        target = stripped[: closing + len("&gt;")]
        remainder = stripped[closing + len("&gt;") :].strip()
    elif stripped.startswith("<"):
        closing = stripped.find(">")
        if closing == -1:
            return None
        target = stripped[: closing + 1]
        remainder = stripped[closing + 1 :].strip()
    else:
        split_at = next(
            (index for index, character in enumerate(stripped) if character.isspace()),
            len(stripped),
        )
        target = stripped[:split_at]
        remainder = stripped[split_at:].strip()
    if remainder and not _valid_markdown_link_title(remainder):
        return None
    return target


def _valid_markdown_link_title(value: str) -> bool:
    if len(value) < 2:
        return False
    pairs: Mapping[str, str] = {'"': '"', "'": "'", "(": ")"}
    closing = pairs.get(value[0])
    return closing is not None and value.endswith(closing)
