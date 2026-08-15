"""Contracts for normalized, redacted, and explicitly untrusted external data."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum

from pangi.application.contracts.redaction import RedactionSummary
from pangi.domain.guardrails import TrustLevel

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_POLICY_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ExternalDataMediaType(StrEnum):
    TEXT = "text/plain"
    HTML = "text/html"


class ExternalDataErrorCode(StrEnum):
    INVALID_MEDIA_TYPE = "external_data_invalid_media_type"
    INPUT_BYTES_EXCEEDED = "external_data_input_bytes_exceeded"
    OUTPUT_BYTES_EXCEEDED = "external_data_output_bytes_exceeded"
    EMPTY_CONTENT = "external_data_empty_content"


@dataclass(frozen=True, slots=True)
class ExternalDataPolicy:
    policy_version: str
    unicode_policy_version: str
    max_input_bytes: int
    max_output_bytes: int
    prohibited_codepoints: frozenset[int]

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.policy_version, "policy_version"),
            (self.unicode_policy_version, "unicode_policy_version"),
        ):
            if _POLICY_IDENTIFIER.fullmatch(identifier) is None:
                raise ValueError(f"{field_name} must be a stable 1-120 character identifier")
        if self.max_input_bytes < 1:
            raise ValueError("max_input_bytes must be positive")
        if self.max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        if not isinstance(self.prohibited_codepoints, frozenset):
            raise ValueError("prohibited_codepoints must be an immutable frozenset")
        if any(codepoint < 0 or codepoint > 0x10FFFF for codepoint in self.prohibited_codepoints):
            raise ValueError("prohibited_codepoints contains an invalid Unicode code point")

    @property
    def fingerprint(self) -> str:
        payload = {
            "max_input_bytes": self.max_input_bytes,
            "max_output_bytes": self.max_output_bytes,
            "policy_version": self.policy_version,
            "prohibited_codepoints": sorted(self.prohibited_codepoints),
            "unicode_policy_version": self.unicode_policy_version,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ExternalDataEnvelope:
    source_kind: str
    original_media_type: ExternalDataMediaType
    content: str = field(repr=False)
    content_fingerprint: str
    content_bytes: int
    normalization_policy_version: str
    normalization_policy_fingerprint: str
    unicode_policy_version: str
    removed_character_count: int
    removed_html_element_count: int
    redaction: RedactionSummary
    trust_level: TrustLevel = field(init=False, default=TrustLevel.UNTRUSTED)

    def __post_init__(self) -> None:
        if _IDENTIFIER.fullmatch(self.source_kind) is None:
            raise ValueError("source_kind must be a stable lowercase identifier")
        try:
            object.__setattr__(
                self,
                "original_media_type",
                ExternalDataMediaType(self.original_media_type),
            )
        except ValueError as error:
            raise ValueError("original_media_type is invalid") from error
        if not self.content.strip():
            raise ValueError("external data content cannot be blank")
        expected_fingerprint = hashlib.sha256(self.content.encode()).hexdigest()
        if (
            _SHA256.fullmatch(self.content_fingerprint) is None
            or self.content_fingerprint != expected_fingerprint
        ):
            raise ValueError("content_fingerprint must match the normalized content SHA-256")
        if self.content_bytes != len(self.content.encode()):
            raise ValueError("content_bytes must match the normalized content")
        for value, field_name in (
            (self.normalization_policy_version, "normalization_policy_version"),
            (self.unicode_policy_version, "unicode_policy_version"),
        ):
            if _POLICY_IDENTIFIER.fullmatch(value) is None:
                raise ValueError(f"{field_name} must be a stable identifier")
        if _SHA256.fullmatch(self.normalization_policy_fingerprint) is None:
            raise ValueError("normalization_policy_fingerprint must be a SHA-256 hex digest")
        if self.removed_character_count < 0 or self.removed_html_element_count < 0:
            raise ValueError("external data removal counts cannot be negative")

    def as_dict(self) -> dict[str, object]:
        return {
            "source_kind": self.source_kind,
            "original_media_type": self.original_media_type.value,
            "trust_level": self.trust_level.value,
            "content_fingerprint": self.content_fingerprint,
            "content_bytes": self.content_bytes,
            "normalization_policy_version": self.normalization_policy_version,
            "normalization_policy_fingerprint": self.normalization_policy_fingerprint,
            "unicode_policy_version": self.unicode_policy_version,
            "removed_character_count": self.removed_character_count,
            "removed_html_element_count": self.removed_html_element_count,
            "redaction": self.redaction.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class RenderedExternalData:
    markup: str = field(repr=False)
    content_fingerprint: str


class ExternalDataError(RuntimeError):
    def __init__(self, code: ExternalDataErrorCode) -> None:
        super().__init__(f"External data rejected: {code.value}")
        self.code = code
