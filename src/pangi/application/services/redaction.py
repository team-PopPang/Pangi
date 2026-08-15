"""Versioned redaction shared by every inbound and outbound boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pangi.application.contracts.redaction import (
    RedactionErrorCode,
    RedactionInputError,
    RedactionPolicy,
    RedactionResult,
    RedactionRule,
    RedactionRuleTarget,
    RedactionSummary,
)

_REDACTED = "[REDACTED]"


def core_secret_redaction_policy() -> RedactionPolicy:
    """Return the immutable built-in policy that preserves the existing CLI boundary."""

    return RedactionPolicy(
        policy_version="core-secret-v1",
        rules=(
            RedactionRule(
                rule_id="authorization-assignment",
                target=RedactionRuleTarget.TEXT,
                pattern=r"\bauthorization\b\s*[:=]\s*(?:bearer\s+)?[^\s,}]+",
                replacement="authorization=[REDACTED]",
                flags=int(re.IGNORECASE),
            ),
            RedactionRule(
                rule_id="credential-assignment",
                target=RedactionRuleTarget.TEXT,
                pattern=(
                    r"\b(?P<label>token|secret|password|api[-_]?key)\b"
                    r"\s*[:=]\s*(?!//)[^\s,}]+"
                ),
                replacement=r"\g<label>=[REDACTED]",
                flags=int(re.IGNORECASE),
            ),
            RedactionRule(
                rule_id="known-token-prefix",
                target=RedactionRuleTarget.TEXT,
                pattern=r"\b(?:sk-[A-Za-z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]+)\b",
                replacement=_REDACTED,
                flags=int(re.IGNORECASE),
            ),
            RedactionRule(
                rule_id="secret-reference",
                target=RedactionRuleTarget.TEXT,
                pattern=r"secret://[^\s\"'<>]+",
                replacement="secret://[REDACTED]",
                flags=int(re.IGNORECASE),
            ),
            RedactionRule(
                rule_id="sensitive-key",
                target=RedactionRuleTarget.KEY,
                pattern=(
                    r"(?:^|[_-])(?:token|secret|password|api[-_]?key|authorization)"
                    r"(?:$|[_-](?:value|hash|header|credential)$)"
                ),
                replacement=_REDACTED,
                flags=int(re.IGNORECASE),
            ),
        ),
        max_depth=32,
        max_collection_items=10_000,
    )


@dataclass(frozen=True, slots=True)
class _CompiledRule:
    contract: RedactionRule
    pattern: re.Pattern[str]


@dataclass(slots=True)
class _RedactionState:
    redaction_count: int = 0
    collection_items: int = 0
    applied_rule_ids: list[str] = field(default_factory=list)
    ancestors: set[int] = field(default_factory=set)

    def record(self, rule_id: str, count: int) -> None:
        self.redaction_count += count
        if count and rule_id not in self.applied_rule_ids:
            self.applied_rule_ids.append(rule_id)


class RedactionService:
    """Apply one immutable rule set without retaining source values."""

    def __init__(self, policy: RedactionPolicy) -> None:
        self._policy = policy
        compiled = tuple(
            _CompiledRule(rule, re.compile(rule.pattern, rule.flags)) for rule in policy.rules
        )
        self._text_rules = tuple(
            rule for rule in compiled if rule.contract.target is RedactionRuleTarget.TEXT
        )
        self._key_rules = tuple(
            rule for rule in compiled if rule.contract.target is RedactionRuleTarget.KEY
        )

    def redact_text(self, value: str) -> RedactionResult:
        state = _RedactionState()
        redacted = self._redact_text(value, state)
        return RedactionResult(redacted, self._summary(state))

    def redact_data(self, value: object) -> RedactionResult:
        state = _RedactionState()
        redacted = self._redact_data(value, depth=0, state=state)
        return RedactionResult(redacted, self._summary(state))

    def _redact_text(self, value: str, state: _RedactionState) -> str:
        redacted = value
        for rule in self._text_rules:
            redacted, count = rule.pattern.subn(rule.contract.replacement, redacted)
            state.record(rule.contract.rule_id, count)
        return redacted

    def _redact_data(
        self,
        value: object,
        *,
        depth: int,
        state: _RedactionState,
    ) -> object:
        if depth > self._policy.max_depth:
            raise RedactionInputError(RedactionErrorCode.INPUT_TOO_DEEP)
        if isinstance(value, str):
            return self._redact_text(value, state)
        if isinstance(value, Mapping):
            self._enter_collection(value, state)
            try:
                redacted_mapping: dict[str, object] = {}
                for item_key, item in value.items():
                    key = str(item_key)
                    key_rule = next(
                        (rule for rule in self._key_rules if rule.pattern.search(key)),
                        None,
                    )
                    if key_rule is not None:
                        redacted_mapping[key] = _REDACTED
                        state.record(key_rule.contract.rule_id, 1)
                        continue
                    safe_key = self._redact_text(key, state)
                    redacted_mapping[safe_key] = self._redact_data(
                        item,
                        depth=depth + 1,
                        state=state,
                    )
                return redacted_mapping
            finally:
                self._leave_collection(value, state)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            self._enter_collection(value, state)
            try:
                return [self._redact_data(item, depth=depth + 1, state=state) for item in value]
            finally:
                self._leave_collection(value, state)
        return value

    def _enter_collection(self, value: object, state: _RedactionState) -> None:
        identifier = id(value)
        if identifier in state.ancestors:
            raise RedactionInputError(RedactionErrorCode.INPUT_CYCLE)
        state.ancestors.add(identifier)
        state.collection_items += len(value)  # type: ignore[arg-type]
        if state.collection_items > self._policy.max_collection_items:
            state.ancestors.remove(identifier)
            raise RedactionInputError(RedactionErrorCode.INPUT_TOO_LARGE)

    @staticmethod
    def _leave_collection(value: object, state: _RedactionState) -> None:
        state.ancestors.remove(id(value))

    def _summary(self, state: _RedactionState) -> RedactionSummary:
        return RedactionSummary(
            policy_version=self._policy.policy_version,
            policy_fingerprint=self._policy.fingerprint,
            redaction_count=state.redaction_count,
            applied_rule_ids=tuple(state.applied_rule_ids),
        )
