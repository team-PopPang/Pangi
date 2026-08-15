"""Versioned normalization and secret redaction for telemetry boundaries."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pangi.application.contracts.redaction import RedactionInputError, RedactionSummary
from pangi.application.contracts.telemetry import (
    SafeLogPayload,
    SafeRunEventPayload,
    TelemetryRedactionError,
    TelemetryRedactionPolicy,
    TelemetryRedactionSummary,
)
from pangi.application.services.redaction import RedactionService, core_secret_redaction_policy
from pangi.domain.telemetry import (
    RUN_EVENT_FORBIDDEN_ATTRIBUTE_KEYS,
    TelemetryRedactionErrorCode,
)


def core_telemetry_redaction_policy() -> TelemetryRedactionPolicy:
    return TelemetryRedactionPolicy(
        policy_version="core-telemetry-v1",
        max_log_message_bytes=8 * 1024,
        max_log_fields_bytes=32 * 1024,
        max_event_message_bytes=8 * 1024,
        max_event_attributes_bytes=64 * 1024,
        max_depth=32,
        max_collection_items=10_000,
        allowed_log_fields=frozenset(
            {
                "count",
                "duration_ms",
                "error_code",
                "event_type",
                "policy_fingerprint",
                "policy_version",
                "request_id",
                "run_id",
                "state",
                "step_id",
            }
        ),
    )


def core_telemetry_redaction_service() -> TelemetryRedactionService:
    return TelemetryRedactionService(
        core_telemetry_redaction_policy(),
        RedactionService(core_secret_redaction_policy()),
    )


class TelemetryRedactionService:
    """Produce bounded safe values without retaining raw telemetry payloads."""

    def __init__(
        self,
        policy: TelemetryRedactionPolicy,
        redactor: RedactionService,
    ) -> None:
        self._policy = policy
        self._redactor = redactor

    def sanitize_log(
        self,
        message: str,
        fields: Mapping[str, object],
        *,
        exception_type: str | None = None,
    ) -> SafeLogPayload:
        selected = {
            key: value
            for key, value in fields.items()
            if isinstance(key, str) and key in self._policy.allowed_log_fields
        }
        dropped_count = len(fields) - len(selected)
        try:
            source, normalized = _normalize_json(
                {
                    "message": message,
                    "fields": selected,
                    "exception_type": exception_type,
                },
                max_depth=self._policy.max_depth,
                max_collection_items=self._policy.max_collection_items,
            )
            if not isinstance(source, dict):
                raise TypeError("normalized log source must be a mapping")
            source_message = source.get("message")
            source_fields = source.get("fields")
            source_exception_type = source.get("exception_type")
            if not isinstance(source_message, str) or not isinstance(source_fields, dict):
                raise TypeError("normalized log source contains invalid values")
            if source_exception_type is not None and not isinstance(
                source_exception_type, str
            ):
                raise TypeError("normalized log source contains an invalid exception type")
            if (
                len(source_message.encode("utf-8"))
                > self._policy.max_log_message_bytes
                or _log_structured_bytes(source_fields, source_exception_type)
                > self._policy.max_log_fields_bytes
            ):
                raise TelemetryRedactionError(
                    TelemetryRedactionErrorCode.LOG_PAYLOAD_TOO_LARGE
                )
            result = self._redactor.redact_data(source)
            payload = result.value
            if not isinstance(payload, dict):
                raise TypeError("redaction result must be a mapping")
            safe_message = payload.get("message")
            safe_fields = payload.get("fields")
            safe_exception_type = payload.get("exception_type")
            if not isinstance(safe_message, str) or not isinstance(safe_fields, dict):
                raise TypeError("redaction result contains invalid log values")
            if safe_exception_type is not None and not isinstance(safe_exception_type, str):
                raise TypeError("redaction result contains an invalid exception type")
            message_bytes = len(safe_message.encode("utf-8"))
            fields_bytes = _log_structured_bytes(safe_fields, safe_exception_type)
        except TelemetryRedactionError:
            raise
        except (RedactionInputError, TypeError, ValueError, UnicodeError):
            raise TelemetryRedactionError(
                TelemetryRedactionErrorCode.REDACTION_FAILED
            ) from None
        if (
            message_bytes > self._policy.max_log_message_bytes
            or fields_bytes > self._policy.max_log_fields_bytes
        ):
            raise TelemetryRedactionError(
                TelemetryRedactionErrorCode.LOG_PAYLOAD_TOO_LARGE
            )
        return SafeLogPayload(
            message=safe_message,
            fields=safe_fields,
            exception_type=safe_exception_type,
            summary=self._summary(
                redaction=result.summary,
                message_bytes=message_bytes,
                structured_bytes=fields_bytes,
                dropped_field_count=dropped_count,
                normalized=normalized,
            ),
        )

    def sanitize_event(
        self,
        message: str | None,
        attributes: Mapping[str, object],
    ) -> SafeRunEventPayload:
        try:
            source, normalized = _normalize_json(
                {"message": message, "attributes": attributes},
                max_depth=self._policy.max_depth,
                max_collection_items=self._policy.max_collection_items,
            )
            if not isinstance(source, dict):
                raise TypeError("normalized event source must be a mapping")
            source_message = source.get("message")
            source_attributes = source.get("attributes")
            if source_message is not None and not isinstance(source_message, str):
                raise TypeError("normalized event source contains an invalid message")
            if not isinstance(source_attributes, dict):
                raise TypeError("normalized event source contains invalid attributes")
            if (
                len((source_message or "").encode("utf-8"))
                > self._policy.max_event_message_bytes
                or _json_bytes(source_attributes)
                > self._policy.max_event_attributes_bytes
            ):
                raise TelemetryRedactionError(
                    TelemetryRedactionErrorCode.EVENT_PAYLOAD_TOO_LARGE
                )
            result = self._redactor.redact_data(source)
            payload = result.value
            if not isinstance(payload, dict):
                raise TypeError("redaction result must be a mapping")
            safe_message = payload.get("message")
            safe_attributes = payload.get("attributes")
            if safe_message is not None and not isinstance(safe_message, str):
                raise TypeError("redaction result contains an invalid event message")
            if not isinstance(safe_attributes, dict):
                raise TypeError("redaction result contains invalid event attributes")
            if _contains_forbidden_event_field(safe_attributes):
                raise TelemetryRedactionError(
                    TelemetryRedactionErrorCode.FORBIDDEN_EVENT_FIELD
                )
            message_bytes = len((safe_message or "").encode("utf-8"))
            attributes_bytes = _json_bytes(safe_attributes)
        except TelemetryRedactionError:
            raise
        except (RedactionInputError, TypeError, ValueError, UnicodeError):
            raise TelemetryRedactionError(
                TelemetryRedactionErrorCode.REDACTION_FAILED
            ) from None
        if (
            message_bytes > self._policy.max_event_message_bytes
            or attributes_bytes > self._policy.max_event_attributes_bytes
        ):
            raise TelemetryRedactionError(
                TelemetryRedactionErrorCode.EVENT_PAYLOAD_TOO_LARGE
            )
        return SafeRunEventPayload(
            message=safe_message,
            attributes=safe_attributes,
            summary=self._summary(
                redaction=result.summary,
                message_bytes=message_bytes,
                structured_bytes=attributes_bytes,
                dropped_field_count=0,
                normalized=normalized,
            ),
        )

    def _summary(
        self,
        *,
        redaction: RedactionSummary,
        message_bytes: int,
        structured_bytes: int,
        dropped_field_count: int,
        normalized: bool,
    ) -> TelemetryRedactionSummary:
        return TelemetryRedactionSummary(
            policy_version=self._policy.policy_version,
            policy_fingerprint=self._policy.fingerprint,
            redaction=redaction,
            message_bytes=message_bytes,
            structured_bytes=structured_bytes,
            dropped_field_count=dropped_field_count,
            normalized=normalized,
        )


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


@dataclass(slots=True)
class _NormalizationState:
    max_depth: int
    max_collection_items: int
    collection_items: int = 0
    ancestors: set[int] = field(default_factory=set)


def _normalize_json(
    value: object,
    *,
    max_depth: int,
    max_collection_items: int,
) -> tuple[object, bool]:
    return _normalize_value(
        value,
        depth=0,
        state=_NormalizationState(max_depth, max_collection_items),
    )


def _normalize_value(
    value: object,
    *,
    depth: int,
    state: _NormalizationState,
) -> tuple[object, bool]:
    if depth > state.max_depth:
        raise TelemetryRedactionError(TelemetryRedactionErrorCode.REDACTION_FAILED)
    if isinstance(value, str):
        normalized = _normalize_text(value)
        return normalized, normalized != value
    if isinstance(value, Mapping):
        _enter_collection(value, state)
        normalized_mapping: dict[str, object] = {}
        changed = False
        try:
            for key, nested in value.items():
                if not isinstance(key, str):
                    raise TelemetryRedactionError(
                        TelemetryRedactionErrorCode.REDACTION_FAILED
                    )
                normalized_key = _normalize_text(key)
                normalized_value, nested_changed = _normalize_value(
                    nested,
                    depth=depth + 1,
                    state=state,
                )
                normalized_mapping[normalized_key] = normalized_value
                changed = changed or normalized_key != key or nested_changed
            return normalized_mapping, changed
        finally:
            state.ancestors.remove(id(value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        _enter_collection(value, state)
        normalized_items: list[object] = []
        changed = False
        try:
            for nested in value:
                normalized_value, nested_changed = _normalize_value(
                    nested,
                    depth=depth + 1,
                    state=state,
                )
                normalized_items.append(normalized_value)
                changed = changed or nested_changed
            return normalized_items, changed
        finally:
            state.ancestors.remove(id(value))
    return value, False


def _enter_collection(value: object, state: _NormalizationState) -> None:
    identifier = id(value)
    if identifier in state.ancestors:
        raise TelemetryRedactionError(TelemetryRedactionErrorCode.REDACTION_FAILED)
    state.ancestors.add(identifier)
    state.collection_items += len(value)  # type: ignore[arg-type]
    if state.collection_items > state.max_collection_items:
        state.ancestors.remove(identifier)
        raise TelemetryRedactionError(TelemetryRedactionErrorCode.REDACTION_FAILED)


def _contains_forbidden_event_field(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in RUN_EVENT_FORBIDDEN_ATTRIBUTE_KEYS:
                return True
            if _contains_forbidden_event_field(nested):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_forbidden_event_field(item) for item in value)
    return False


def _json_bytes(value: object) -> int:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return len(encoded)


def _log_structured_bytes(
    fields: Mapping[str, object],
    exception_type: str | None,
) -> int:
    return _json_bytes(
        {
            "exception_type": exception_type,
            "fields": fields,
        }
    )
