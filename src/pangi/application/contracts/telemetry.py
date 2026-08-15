"""Secret-safe contracts for logs and persisted Run Events."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from pangi.application.contracts.redaction import RedactionSummary
from pangi.domain.telemetry import TelemetryRedactionErrorCode

_POLICY_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_LOG_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


@dataclass(frozen=True, slots=True)
class TelemetryRedactionPolicy:
    policy_version: str
    max_log_message_bytes: int
    max_log_fields_bytes: int
    max_event_message_bytes: int
    max_event_attributes_bytes: int
    max_depth: int
    max_collection_items: int
    allowed_log_fields: frozenset[str]

    def __post_init__(self) -> None:
        if _POLICY_IDENTIFIER.fullmatch(self.policy_version) is None:
            raise ValueError("policy_version must be a stable identifier")
        for field_name in (
            "max_log_message_bytes",
            "max_log_fields_bytes",
            "max_event_message_bytes",
            "max_event_attributes_bytes",
        ):
            value = getattr(self, field_name)
            if not 1 <= value <= 16 * 1024 * 1024:
                raise ValueError(f"{field_name} must be between 1 byte and 16 MiB")
        if not 1 <= self.max_depth <= 100:
            raise ValueError("max_depth must be between 1 and 100")
        if not 1 <= self.max_collection_items <= 100_000:
            raise ValueError("max_collection_items must be between 1 and 100000")
        if not isinstance(self.allowed_log_fields, frozenset):
            raise ValueError("allowed_log_fields must be an immutable frozenset")
        if any(_LOG_FIELD.fullmatch(name) is None for name in self.allowed_log_fields):
            raise ValueError("allowed_log_fields contains an invalid field name")

    @property
    def fingerprint(self) -> str:
        payload = {
            "allowed_log_fields": sorted(self.allowed_log_fields),
            "max_event_attributes_bytes": self.max_event_attributes_bytes,
            "max_event_message_bytes": self.max_event_message_bytes,
            "max_collection_items": self.max_collection_items,
            "max_depth": self.max_depth,
            "max_log_fields_bytes": self.max_log_fields_bytes,
            "max_log_message_bytes": self.max_log_message_bytes,
            "policy_version": self.policy_version,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class TelemetryRedactionSummary:
    policy_version: str
    policy_fingerprint: str
    redaction: RedactionSummary
    message_bytes: int
    structured_bytes: int
    dropped_field_count: int
    normalized: bool

    def __post_init__(self) -> None:
        if _POLICY_IDENTIFIER.fullmatch(self.policy_version) is None:
            raise ValueError("policy_version must be a stable identifier")
        if re.fullmatch(r"[0-9a-f]{64}", self.policy_fingerprint) is None:
            raise ValueError("policy_fingerprint must be a SHA-256 hex digest")
        for value, name in (
            (self.message_bytes, "message_bytes"),
            (self.structured_bytes, "structured_bytes"),
            (self.dropped_field_count, "dropped_field_count"),
        ):
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if not isinstance(self.normalized, bool):
            raise ValueError("normalized must be a boolean")

    @property
    def changed(self) -> bool:
        return bool(
            self.redaction.redaction_count or self.dropped_field_count or self.normalized
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "redaction": self.redaction.as_dict(),
            "message_bytes": self.message_bytes,
            "structured_bytes": self.structured_bytes,
            "dropped_field_count": self.dropped_field_count,
            "normalized": self.normalized,
        }


@dataclass(frozen=True, slots=True)
class SafeLogPayload:
    message: str = field(repr=False)
    fields: Mapping[str, object] = field(repr=False)
    exception_type: str | None
    summary: TelemetryRedactionSummary

    def __post_init__(self) -> None:
        if not isinstance(self.message, str):
            raise ValueError("safe log message must be a string")
        if self.exception_type is not None and not isinstance(self.exception_type, str):
            raise ValueError("exception_type must be a string")
        object.__setattr__(self, "fields", _freeze_mapping(self.fields))


@dataclass(frozen=True, slots=True)
class SafeRunEventPayload:
    message: str | None = field(repr=False)
    attributes: Mapping[str, object] = field(repr=False)
    summary: TelemetryRedactionSummary

    def __post_init__(self) -> None:
        if self.message is not None and not isinstance(self.message, str):
            raise ValueError("safe event message must be a string or None")
        object.__setattr__(self, "attributes", _freeze_mapping(self.attributes))


class TelemetryRedactionError(RuntimeError):
    def __init__(self, code: TelemetryRedactionErrorCode) -> None:
        super().__init__(f"Telemetry payload rejected: {code.value}")
        self.code = code


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value
