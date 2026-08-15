"""Secret-safe input-guardrail policy and result contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum

from pangi.application.contracts.runs import RunCreation
from pangi.domain.guardrails import (
    GuardrailErrorCode,
    GuardrailOutcome,
    GuardrailStage,
    TrustLevel,
)
from pangi.domain.runs import RunRequest

_POLICY_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$")


class ExplicitSkillAccess(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class InputGuardrailPolicy:
    """An injected policy with no organization-wide runtime defaults."""

    policy_version: str
    unicode_policy_version: str
    max_text_bytes: int
    max_attachment_count: int
    max_attachment_bytes: int
    max_total_attachment_bytes: int
    allowed_media_types: frozenset[str]
    prohibited_codepoints: frozenset[int]
    rate_limit: int
    rate_window_seconds: int

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.policy_version, "policy_version"),
            (self.unicode_policy_version, "unicode_policy_version"),
        ):
            if _POLICY_IDENTIFIER.fullmatch(identifier) is None:
                raise ValueError(f"{field_name} must be a stable 1-120 character identifier")
        if self.max_text_bytes < 1:
            raise ValueError("max_text_bytes must be positive")
        for limit, field_name in (
            (self.max_attachment_count, "max_attachment_count"),
            (self.max_attachment_bytes, "max_attachment_bytes"),
            (self.max_total_attachment_bytes, "max_total_attachment_bytes"),
        ):
            if limit < 0:
                raise ValueError(f"{field_name} cannot be negative")
        if not isinstance(self.allowed_media_types, frozenset):
            raise ValueError("allowed_media_types must be an immutable frozenset")
        normalized_media_types = frozenset(
            media_type.strip().casefold() for media_type in self.allowed_media_types
        )
        if any(_MEDIA_TYPE.fullmatch(media_type) is None for media_type in normalized_media_types):
            raise ValueError("allowed_media_types contains an invalid media type")
        object.__setattr__(self, "allowed_media_types", normalized_media_types)
        if not isinstance(self.prohibited_codepoints, frozenset):
            raise ValueError("prohibited_codepoints must be an immutable frozenset")
        if any(codepoint < 0 or codepoint > 0x10FFFF for codepoint in self.prohibited_codepoints):
            raise ValueError("prohibited_codepoints contains an invalid Unicode code point")
        if self.rate_limit < 1:
            raise ValueError("rate_limit must be positive")
        if self.rate_window_seconds < 1:
            raise ValueError("rate_window_seconds must be positive")

    @property
    def fingerprint(self) -> str:
        payload = {
            "allowed_media_types": sorted(self.allowed_media_types),
            "max_attachment_bytes": self.max_attachment_bytes,
            "max_attachment_count": self.max_attachment_count,
            "max_text_bytes": self.max_text_bytes,
            "max_total_attachment_bytes": self.max_total_attachment_bytes,
            "policy_version": self.policy_version,
            "prohibited_codepoints": sorted(self.prohibited_codepoints),
            "rate_limit": self.rate_limit,
            "rate_window_seconds": self.rate_window_seconds,
            "unicode_policy_version": self.unicode_policy_version,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class GuardrailDecision:
    """A result containing policy metadata but no request content or identifiers."""

    trust_level: TrustLevel
    stage: GuardrailStage
    outcome: GuardrailOutcome
    policy_version: str
    policy_fingerprint: str
    unicode_policy_version: str
    text_bytes: int | None = None
    attachment_count: int = 0
    error_code: GuardrailErrorCode | None = None
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "trust_level", TrustLevel(self.trust_level))
            object.__setattr__(self, "stage", GuardrailStage(self.stage))
            object.__setattr__(self, "outcome", GuardrailOutcome(self.outcome))
            if self.error_code is not None:
                object.__setattr__(
                    self,
                    "error_code",
                    GuardrailErrorCode(self.error_code),
                )
        except ValueError as error:
            raise ValueError("guardrail decision contains an invalid enum value") from error
        if _POLICY_IDENTIFIER.fullmatch(self.policy_version) is None:
            raise ValueError("policy_version must be a stable identifier")
        if _POLICY_IDENTIFIER.fullmatch(self.unicode_policy_version) is None:
            raise ValueError("unicode_policy_version must be a stable identifier")
        if re.fullmatch(r"[0-9a-f]{64}", self.policy_fingerprint) is None:
            raise ValueError("policy_fingerprint must be a SHA-256 hex digest")
        if self.text_bytes is not None and self.text_bytes < 0:
            raise ValueError("text_bytes cannot be negative")
        if self.attachment_count < 0:
            raise ValueError("attachment_count cannot be negative")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 1:
            raise ValueError("retry_after_seconds must be positive")
        if self.outcome is GuardrailOutcome.ALLOWED:
            if self.stage is not GuardrailStage.COMPLETE:
                raise ValueError("an allowed decision must be complete")
            if self.error_code is not None or self.retry_after_seconds is not None:
                raise ValueError("an allowed decision cannot contain rejection metadata")
        elif self.stage is GuardrailStage.COMPLETE or self.error_code is None:
            raise ValueError("a blocked decision requires a rejection stage and error code")

    def as_dict(self) -> dict[str, object]:
        return {
            "trust_level": self.trust_level.value,
            "stage": self.stage.value,
            "outcome": self.outcome.value,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "unicode_policy_version": self.unicode_policy_version,
            "text_bytes": self.text_bytes,
            "attachment_count": self.attachment_count,
            "error_code": self.error_code.value if self.error_code is not None else None,
            "retry_after_seconds": self.retry_after_seconds,
        }


@dataclass(frozen=True, slots=True)
class GuardedRunRequest:
    """A normalized request whose repr cannot disclose its untrusted payload."""

    request: RunRequest = field(repr=False)
    decision: GuardrailDecision


@dataclass(frozen=True, slots=True)
class GuardedRunCreation:
    """A persisted result paired with the policy decision that admitted it."""

    creation: RunCreation = field(repr=False)
    decision: GuardrailDecision


class GuardrailBlockedError(RuntimeError):
    """A deterministic rejection whose message contains no untrusted input."""

    def __init__(self, decision: GuardrailDecision) -> None:
        if decision.error_code is None:
            raise ValueError("A blocked guardrail decision requires an error code")
        super().__init__(f"Input guardrail blocked request: {decision.error_code.value}")
        self.decision = decision

    @property
    def code(self) -> GuardrailErrorCode:
        assert self.decision.error_code is not None
        return self.decision.error_code
