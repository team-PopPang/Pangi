"""Secret-safe contracts for deterministic final Output sanitization."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from pangi.application.contracts.redaction import RedactionSummary
from pangi.domain.guardrails import TrustLevel
from pangi.domain.output_guardrails import (
    OutputGuardrailErrorCode,
    OutputGuardrailOutcome,
    OutputGuardrailStage,
)

_POLICY_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_REGEX_FLAGS = re.IGNORECASE | re.MULTILINE | re.DOTALL | re.ASCII
_FORBIDDEN_LINK_SCHEMES = frozenset({"data", "file", "javascript", "vbscript"})


def _validate_fingerprint(value: str, *, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")


@dataclass(frozen=True, slots=True)
class OutputSanitizationRule:
    rule_id: str
    pattern: str = field(repr=False)
    replacement: str = field(repr=False)
    flags: int = 0

    def __post_init__(self) -> None:
        if _POLICY_IDENTIFIER.fullmatch(self.rule_id) is None:
            raise ValueError("rule_id must be a stable identifier")
        if not 1 <= len(self.pattern) <= 4_000:
            raise ValueError("output sanitization pattern must contain 1-4000 characters")
        if not 1 <= len(self.replacement) <= 255:
            raise ValueError("output sanitization replacement must contain 1-255 characters")
        if self.flags < 0 or self.flags & ~int(_ALLOWED_REGEX_FLAGS):
            raise ValueError("output sanitization rule contains unsupported regex flags")
        try:
            compiled = re.compile(self.pattern, self.flags)
            compiled.sub(self.replacement, "output-contract-check")
        except re.error as error:
            raise ValueError("output sanitization rule regex is invalid") from error
        if compiled.search("") is not None:
            raise ValueError("output sanitization rule cannot match an empty string")


@dataclass(frozen=True, slots=True)
class OutputGuardrailPolicy:
    policy_version: str
    max_input_bytes: int
    max_output_bytes: int
    max_mentions: int
    max_evidence_links: int
    max_evidence_link_bytes: int
    allowed_link_schemes: frozenset[str]
    allow_relative_links: bool
    broadcast_mentions: frozenset[str]
    internal_detail_rules: tuple[OutputSanitizationRule, ...]
    truncation_marker: str = field(repr=False)

    def __post_init__(self) -> None:
        if _POLICY_IDENTIFIER.fullmatch(self.policy_version) is None:
            raise ValueError("policy_version must be a stable identifier")
        if self.max_input_bytes < 1:
            raise ValueError("max_input_bytes must be positive")
        if self.max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        if self.max_input_bytes < self.max_output_bytes:
            raise ValueError("max_input_bytes cannot be smaller than max_output_bytes")
        if self.max_mentions < 0:
            raise ValueError("max_mentions cannot be negative")
        if self.max_evidence_links < 0:
            raise ValueError("max_evidence_links cannot be negative")
        if not 16 <= self.max_evidence_link_bytes <= 8_192:
            raise ValueError("max_evidence_link_bytes must be between 16 and 8192")
        if not isinstance(self.allowed_link_schemes, frozenset):
            raise ValueError("allowed_link_schemes must be an immutable frozenset")
        normalized_schemes = frozenset(
            scheme.strip().casefold() for scheme in self.allowed_link_schemes
        )
        if any(_SCHEME.fullmatch(scheme) is None for scheme in normalized_schemes):
            raise ValueError("allowed_link_schemes contains an invalid URI scheme")
        if normalized_schemes & _FORBIDDEN_LINK_SCHEMES:
            raise ValueError("allowed_link_schemes contains an unsafe URI scheme")
        object.__setattr__(self, "allowed_link_schemes", normalized_schemes)
        if not isinstance(self.allow_relative_links, bool):
            raise ValueError("allow_relative_links must be a boolean")
        if not isinstance(self.broadcast_mentions, frozenset):
            raise ValueError("broadcast_mentions must be an immutable frozenset")
        normalized_mentions = frozenset(
            mention.strip().casefold() for mention in self.broadcast_mentions
        )
        if not normalized_mentions or any(
            re.fullmatch(r"@[a-z][a-z0-9_-]{0,79}", mention) is None
            for mention in normalized_mentions
        ):
            raise ValueError("broadcast_mentions contains an invalid mention")
        object.__setattr__(self, "broadcast_mentions", normalized_mentions)
        if not isinstance(self.internal_detail_rules, tuple) or not self.internal_detail_rules:
            raise ValueError("internal_detail_rules must be a non-empty immutable tuple")
        rule_ids = tuple(rule.rule_id for rule in self.internal_detail_rules)
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("internal_detail_rules cannot contain duplicate identifiers")
        marker_bytes = self.truncation_marker.encode("utf-8")
        if (
            not self.truncation_marker.strip()
            or not self.truncation_marker.startswith("\n")
            or len(marker_bytes) >= self.max_output_bytes
            or any(character in self.truncation_marker for character in "\r<>@()")
        ):
            raise ValueError("truncation_marker must be safe and smaller than max_output_bytes")

    @property
    def fingerprint(self) -> str:
        payload = {
            "allow_relative_links": self.allow_relative_links,
            "allowed_link_schemes": sorted(self.allowed_link_schemes),
            "broadcast_mentions": sorted(self.broadcast_mentions),
            "internal_detail_rules": [
                {
                    "flags": rule.flags,
                    "pattern": rule.pattern,
                    "replacement": rule.replacement,
                    "rule_id": rule.rule_id,
                }
                for rule in self.internal_detail_rules
            ],
            "max_evidence_link_bytes": self.max_evidence_link_bytes,
            "max_evidence_links": self.max_evidence_links,
            "max_input_bytes": self.max_input_bytes,
            "max_mentions": self.max_mentions,
            "max_output_bytes": self.max_output_bytes,
            "policy_version": self.policy_version,
            "truncation_marker": self.truncation_marker,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class OutputCandidate:
    markdown: str = field(repr=False)
    evidence_links: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.markdown, str):
            raise ValueError("markdown must be a string")
        if not isinstance(self.evidence_links, tuple):
            raise ValueError("evidence_links must be an immutable tuple")
        if any(not isinstance(link, str) for link in self.evidence_links):
            raise ValueError("evidence_links must contain only strings")


@dataclass(frozen=True, slots=True)
class OutputGuardrailDecision:
    stage: OutputGuardrailStage
    outcome: OutputGuardrailOutcome
    policy_version: str
    policy_fingerprint: str
    input_bytes: int
    error_code: OutputGuardrailErrorCode | None = None
    trust_level: TrustLevel = field(init=False, default=TrustLevel.UNTRUSTED)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "stage", OutputGuardrailStage(self.stage))
            object.__setattr__(self, "outcome", OutputGuardrailOutcome(self.outcome))
            if self.error_code is not None:
                object.__setattr__(
                    self,
                    "error_code",
                    OutputGuardrailErrorCode(self.error_code),
                )
        except ValueError as error:
            raise ValueError("Output guardrail decision contains an invalid enum value") from error
        if _POLICY_IDENTIFIER.fullmatch(self.policy_version) is None:
            raise ValueError("policy_version must be a stable identifier")
        _validate_fingerprint(self.policy_fingerprint, field_name="policy_fingerprint")
        if self.input_bytes < 0:
            raise ValueError("input_bytes cannot be negative")
        if self.outcome is OutputGuardrailOutcome.ALLOWED:
            if self.stage is not OutputGuardrailStage.COMPLETE or self.error_code is not None:
                raise ValueError("an allowed Output decision must be complete")
        elif self.stage is OutputGuardrailStage.COMPLETE or self.error_code is None:
            raise ValueError("a blocked Output decision requires a stage and error code")

    def as_dict(self) -> dict[str, object]:
        return {
            "trust_level": self.trust_level.value,
            "stage": self.stage.value,
            "outcome": self.outcome.value,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "input_bytes": self.input_bytes,
            "error_code": self.error_code.value if self.error_code is not None else None,
        }


@dataclass(frozen=True, slots=True)
class OutputGuardrailSummary:
    redaction: RedactionSummary
    output_bytes: int
    internal_detail_count: int
    applied_internal_rule_ids: tuple[str, ...]
    escaped_html_character_count: int
    removed_markdown_link_count: int
    removed_evidence_link_count: int
    neutralized_mention_count: int
    truncated: bool

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.output_bytes, "output_bytes"),
            (self.internal_detail_count, "internal_detail_count"),
            (self.escaped_html_character_count, "escaped_html_character_count"),
            (self.removed_markdown_link_count, "removed_markdown_link_count"),
            (self.removed_evidence_link_count, "removed_evidence_link_count"),
            (self.neutralized_mention_count, "neutralized_mention_count"),
        ):
            if value < 0:
                raise ValueError(f"{field_name} cannot be negative")
        if not isinstance(self.applied_internal_rule_ids, tuple):
            raise ValueError("applied_internal_rule_ids must be an immutable tuple")
        if len(set(self.applied_internal_rule_ids)) != len(self.applied_internal_rule_ids):
            raise ValueError("applied_internal_rule_ids cannot contain duplicates")
        if any(
            _POLICY_IDENTIFIER.fullmatch(rule_id) is None
            for rule_id in self.applied_internal_rule_ids
        ):
            raise ValueError("applied_internal_rule_ids contains an invalid identifier")
        if self.internal_detail_count == 0 and self.applied_internal_rule_ids:
            raise ValueError("unchanged internal details cannot contain applied rules")
        if not isinstance(self.truncated, bool):
            raise ValueError("truncated must be a boolean")

    @property
    def changed(self) -> bool:
        return any(
            (
                self.redaction.redaction_count,
                self.internal_detail_count,
                self.escaped_html_character_count,
                self.removed_markdown_link_count,
                self.removed_evidence_link_count,
                self.neutralized_mention_count,
                int(self.truncated),
            )
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "redaction": self.redaction.as_dict(),
            "output_bytes": self.output_bytes,
            "internal_detail_count": self.internal_detail_count,
            "applied_internal_rule_ids": list(self.applied_internal_rule_ids),
            "escaped_html_character_count": self.escaped_html_character_count,
            "removed_markdown_link_count": self.removed_markdown_link_count,
            "removed_evidence_link_count": self.removed_evidence_link_count,
            "neutralized_mention_count": self.neutralized_mention_count,
            "truncated": self.truncated,
            "changed": self.changed,
        }


@dataclass(frozen=True, slots=True)
class SafeOutput:
    markdown: str = field(repr=False)
    evidence_links: tuple[str, ...] = field(repr=False)
    content_fingerprint: str
    decision: OutputGuardrailDecision
    summary: OutputGuardrailSummary

    def __post_init__(self) -> None:
        if self.decision.outcome is not OutputGuardrailOutcome.ALLOWED:
            raise ValueError("SafeOutput requires an allowed decision")
        if not self.markdown.strip():
            raise ValueError("SafeOutput markdown cannot be blank")
        if not isinstance(self.evidence_links, tuple):
            raise ValueError("SafeOutput evidence_links must be an immutable tuple")
        if self.summary.output_bytes != len(self.markdown.encode("utf-8")):
            raise ValueError("summary output_bytes must match SafeOutput markdown")
        payload = json.dumps(
            {
                "evidence_links": list(self.evidence_links),
                "markdown": self.markdown,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        expected_fingerprint = hashlib.sha256(payload).hexdigest()
        if self.content_fingerprint != expected_fingerprint:
            raise ValueError("content_fingerprint must match the safe Output")

    def as_metadata(self) -> dict[str, object]:
        return {
            "content_fingerprint": self.content_fingerprint,
            "decision": self.decision.as_dict(),
            "summary": self.summary.as_dict(),
            "evidence_link_count": len(self.evidence_links),
        }


class OutputGuardrailBlockedError(RuntimeError):
    """A deterministic rejection whose message contains no proposed Output."""

    def __init__(self, decision: OutputGuardrailDecision) -> None:
        if decision.error_code is None:
            raise ValueError("a blocked Output decision requires an error code")
        super().__init__(f"Output guardrail blocked content: {decision.error_code.value}")
        self.decision = decision

    @property
    def code(self) -> OutputGuardrailErrorCode:
        assert self.decision.error_code is not None
        return self.decision.error_code
