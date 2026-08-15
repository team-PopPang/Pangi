"""Secret-safe redaction policy, summary, and result contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum

_POLICY_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_REGEX_FLAGS = re.IGNORECASE | re.MULTILINE | re.DOTALL


class RedactionRuleTarget(StrEnum):
    TEXT = "text"
    KEY = "key"


class RedactionErrorCode(StrEnum):
    INPUT_TOO_DEEP = "redaction_input_too_deep"
    INPUT_TOO_LARGE = "redaction_input_too_large"
    INPUT_CYCLE = "redaction_input_cycle"


@dataclass(frozen=True, slots=True)
class RedactionRule:
    rule_id: str
    target: RedactionRuleTarget
    pattern: str = field(repr=False)
    replacement: str = field(repr=False)
    flags: int = 0

    def __post_init__(self) -> None:
        if _POLICY_IDENTIFIER.fullmatch(self.rule_id) is None:
            raise ValueError("rule_id must be a stable 1-120 character identifier")
        try:
            object.__setattr__(self, "target", RedactionRuleTarget(self.target))
        except ValueError as error:
            raise ValueError("redaction rule target is invalid") from error
        if not self.pattern or len(self.pattern) > 2_000:
            raise ValueError("redaction rule pattern must contain 1-2000 characters")
        if not self.replacement or len(self.replacement) > 255:
            raise ValueError("redaction rule replacement must contain 1-255 characters")
        if self.flags < 0 or self.flags & ~int(_ALLOWED_REGEX_FLAGS):
            raise ValueError("redaction rule contains unsupported regex flags")
        try:
            compiled = re.compile(self.pattern, self.flags)
            compiled.sub(self.replacement, "redaction-contract-check")
        except re.error as error:
            raise ValueError("redaction rule regex is invalid") from error
        if compiled.search("") is not None:
            raise ValueError("redaction rule cannot match an empty string")


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    policy_version: str
    rules: tuple[RedactionRule, ...]
    max_depth: int
    max_collection_items: int

    def __post_init__(self) -> None:
        if _POLICY_IDENTIFIER.fullmatch(self.policy_version) is None:
            raise ValueError("policy_version must be a stable 1-120 character identifier")
        if not isinstance(self.rules, tuple) or not self.rules:
            raise ValueError("redaction rules must be a non-empty immutable tuple")
        rule_ids = tuple(rule.rule_id for rule in self.rules)
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("redaction rule identifiers must be unique")
        if not 1 <= self.max_depth <= 100:
            raise ValueError("max_depth must be between 1 and 100")
        if not 1 <= self.max_collection_items <= 100_000:
            raise ValueError("max_collection_items must be between 1 and 100000")

    @property
    def fingerprint(self) -> str:
        payload = {
            "max_collection_items": self.max_collection_items,
            "max_depth": self.max_depth,
            "policy_version": self.policy_version,
            "rules": [
                {
                    "flags": rule.flags,
                    "pattern": rule.pattern,
                    "replacement": rule.replacement,
                    "rule_id": rule.rule_id,
                    "target": rule.target.value,
                }
                for rule in self.rules
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class RedactionSummary:
    policy_version: str
    policy_fingerprint: str
    redaction_count: int
    applied_rule_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if _POLICY_IDENTIFIER.fullmatch(self.policy_version) is None:
            raise ValueError("policy_version must be a stable identifier")
        if _SHA256.fullmatch(self.policy_fingerprint) is None:
            raise ValueError("policy_fingerprint must be a SHA-256 hex digest")
        if self.redaction_count < 0:
            raise ValueError("redaction_count cannot be negative")
        if not isinstance(self.applied_rule_ids, tuple):
            raise ValueError("applied_rule_ids must be an immutable tuple")
        if len(set(self.applied_rule_ids)) != len(self.applied_rule_ids):
            raise ValueError("applied_rule_ids cannot contain duplicates")
        if any(_POLICY_IDENTIFIER.fullmatch(rule_id) is None for rule_id in self.applied_rule_ids):
            raise ValueError("applied_rule_ids contains an invalid identifier")
        if self.redaction_count == 0 and self.applied_rule_ids:
            raise ValueError("an unchanged result cannot contain applied rules")

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "redaction_count": self.redaction_count,
            "applied_rule_ids": list(self.applied_rule_ids),
        }


@dataclass(frozen=True, slots=True)
class RedactionResult:
    value: object = field(repr=False)
    summary: RedactionSummary


class RedactionInputError(RuntimeError):
    def __init__(self, code: RedactionErrorCode) -> None:
        super().__init__(f"Redaction input rejected: {code.value}")
        self.code = code
