"""Versioned Audit sanitization and administrator-only query use cases."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pangi.application.contracts.audit import (
    AuditCursorPosition,
    AuditEventDraft,
    AuditListPage,
    AuditListQuery,
    AuditPolicy,
    AuditRedactionError,
    AuditRedactionErrorCode,
    AuditStoreQuery,
)
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.redaction import RedactionInputError
from pangi.application.ports.audit import AuditEventStore, InvalidAuditCursorError
from pangi.application.ports.auth import PermissionDeniedError
from pangi.application.services.redaction import RedactionService, core_secret_redaction_policy
from pangi.domain.audit import AuditEvent
from pangi.domain.auth import UserRole, UserStatus

_CURSOR_VERSION = 1
_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_RESOURCE_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,119}$")


def core_audit_policy() -> AuditPolicy:
    return AuditPolicy(
        policy_version="core-audit-v1",
        max_metadata_bytes=64 * 1024,
        max_depth=32,
        max_collection_items=10_000,
        retention_days=365,
    )


def core_audit_redaction_service() -> AuditRedactionService:
    return AuditRedactionService(
        core_audit_policy(),
        RedactionService(core_secret_redaction_policy()),
    )


@dataclass(slots=True)
class _NormalizationState:
    collection_items: int = 0
    normalized: bool = False
    ancestors: set[int] = field(default_factory=set)


class AuditRedactionService:
    """Convert one raw management change into a bounded safe Audit Event."""

    def __init__(self, policy: AuditPolicy, redactor: RedactionService) -> None:
        self._policy = policy
        self._redactor = redactor

    @property
    def policy(self) -> AuditPolicy:
        return self._policy

    def prepare(self, draft: AuditEventDraft, *, event_id: str) -> AuditEvent:
        try:
            source, normalized = self._normalize(
                {
                    "before": draft.before_summary,
                    "after": draft.after_summary,
                    "details": draft.details,
                }
            )
            source_bytes = len(_canonical_json(source).encode("utf-8"))
            if source_bytes > self._policy.max_metadata_bytes:
                raise AuditRedactionError(AuditRedactionErrorCode.INPUT_TOO_LARGE)
            result = self._redactor.redact_data(source)
            safe = result.value
            if not isinstance(safe, dict):
                raise TypeError("audit redaction result must be a mapping")
            before = _snapshot(safe.get("before"))
            after = _snapshot(safe.get("after"))
            details_value = safe.get("details")
            if not isinstance(details_value, dict):
                raise TypeError("audit details must be a mapping")
            details_fingerprint = _fingerprint(details_value)
            change_fingerprint = _fingerprint(
                {
                    "action": draft.action,
                    "after": None if after is None else after["fingerprint"],
                    "before": None if before is None else before["fingerprint"],
                    "details": details_fingerprint,
                    "outcome": draft.outcome.value,
                    "policy_fingerprint": self._policy.fingerprint,
                    "resource_id": draft.resource_id,
                    "resource_type": draft.resource_type,
                }
            )
            metadata: dict[str, object] = {
                "schema_version": 1,
                "outcome": draft.outcome.value,
                "before": before,
                "after": after,
                "details": details_value,
                "details_fingerprint": details_fingerprint,
                "change_fingerprint": change_fingerprint,
                "policy": {
                    "version": self._policy.policy_version,
                    "fingerprint": self._policy.fingerprint,
                    "retention_days": self._policy.retention_days,
                },
                "redaction": result.summary.as_dict(),
                "normalized": normalized,
            }
            if len(_canonical_json(metadata).encode("utf-8")) > self._policy.max_metadata_bytes:
                raise AuditRedactionError(AuditRedactionErrorCode.INPUT_TOO_LARGE)
            return AuditEvent(
                id=event_id,
                actor_id=draft.actor_id,
                action=draft.action,
                resource_type=draft.resource_type,
                resource_id=draft.resource_id,
                outcome=draft.outcome,
                metadata=metadata,
                created_at=draft.created_at,
            )
        except AuditRedactionError:
            raise
        except RedactionInputError:
            raise AuditRedactionError(AuditRedactionErrorCode.REDACTION_FAILED) from None
        except (TypeError, ValueError, UnicodeError):
            raise AuditRedactionError(AuditRedactionErrorCode.INPUT_INVALID) from None

    def _normalize(self, value: object) -> tuple[object, bool]:
        state = _NormalizationState()
        normalized = self._normalize_value(value, depth=0, state=state)
        return normalized, state.normalized

    def _normalize_value(
        self,
        value: object,
        *,
        depth: int,
        state: _NormalizationState,
    ) -> object:
        if depth > self._policy.max_depth:
            raise AuditRedactionError(AuditRedactionErrorCode.INPUT_TOO_DEEP)
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise AuditRedactionError(AuditRedactionErrorCode.INPUT_INVALID)
            return value
        if isinstance(value, str):
            line_normalized = value.replace("\r\n", "\n").replace("\r", "\n")
            normalized = unicodedata.normalize("NFC", line_normalized)
            state.normalized = state.normalized or normalized != value
            return normalized
        if isinstance(value, Mapping):
            self._enter(value, state)
            try:
                normalized_mapping: dict[str, object] = {}
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise AuditRedactionError(AuditRedactionErrorCode.INPUT_INVALID)
                    safe_key = self._normalize_value(key, depth=depth + 1, state=state)
                    if not isinstance(safe_key, str) or safe_key in normalized_mapping:
                        raise AuditRedactionError(AuditRedactionErrorCode.INPUT_INVALID)
                    normalized_mapping[safe_key] = self._normalize_value(
                        item,
                        depth=depth + 1,
                        state=state,
                    )
                return normalized_mapping
            finally:
                self._leave(value, state)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            self._enter(value, state)
            try:
                return [
                    self._normalize_value(item, depth=depth + 1, state=state)
                    for item in value
                ]
            finally:
                self._leave(value, state)
        raise AuditRedactionError(AuditRedactionErrorCode.INPUT_INVALID)

    def _enter(self, value: object, state: _NormalizationState) -> None:
        identifier = id(value)
        if identifier in state.ancestors:
            raise AuditRedactionError(AuditRedactionErrorCode.INPUT_CYCLE)
        state.ancestors.add(identifier)
        state.collection_items += len(value)  # type: ignore[arg-type]
        if state.collection_items > self._policy.max_collection_items:
            state.ancestors.remove(identifier)
            raise AuditRedactionError(AuditRedactionErrorCode.INPUT_TOO_LARGE)

    @staticmethod
    def _leave(value: object, state: _NormalizationState) -> None:
        state.ancestors.remove(id(value))


class AuditQueryService:
    """Authorize and paginate immutable Audit Events for active administrators."""

    def __init__(self, store: AuditEventStore) -> None:
        self._store = store

    async def list_events(
        self,
        *,
        actor: AuthenticatedPrincipal,
        query: AuditListQuery,
    ) -> AuditListPage:
        if actor.status is not UserStatus.ACTIVE or actor.role is not UserRole.ADMIN:
            raise PermissionDeniedError("The authenticated role is not allowed")
        _validate_query_filters(query)
        query_fingerprint = _query_fingerprint(actor, query)
        after = (
            _decode_cursor(query.cursor, query_fingerprint=query_fingerprint)
            if query.cursor is not None
            else None
        )
        fetched = await self._store.list_events(
            AuditStoreQuery(
                actor_id=query.actor_id,
                actions=query.actions,
                resource_type=query.resource_type,
                resource_id=query.resource_id,
                outcomes=query.outcomes,
                created_from=query.created_from,
                created_to=query.created_to,
                limit=query.limit + 1,
                after=after,
            )
        )
        items = fetched[: query.limit]
        next_cursor = None
        if len(fetched) > query.limit and items:
            last = items[-1]
            next_cursor = _encode_cursor(
                AuditCursorPosition(last.created_at, last.id),
                query_fingerprint=query_fingerprint,
            )
        return AuditListPage(items=items, next_cursor=next_cursor)


def _validate_query_filters(query: AuditListQuery) -> None:
    if any(_ACTION.fullmatch(action) is None for action in query.actions):
        raise InvalidAuditCursorError("The Audit filter is invalid")
    if query.resource_type is not None and _RESOURCE_TYPE.fullmatch(query.resource_type) is None:
        raise InvalidAuditCursorError("The Audit filter is invalid")
    for value in (query.actor_id, query.resource_id):
        if value is not None and _EVENT_ID.fullmatch(value) is None:
            raise InvalidAuditCursorError("The Audit filter is invalid")


def _query_fingerprint(actor: AuthenticatedPrincipal, query: AuditListQuery) -> str:
    return _fingerprint(
        {
            "actions": sorted(query.actions),
            "actor_id": query.actor_id,
            "actor_user_id": actor.user_id,
            "created_from": (
                query.created_from.isoformat() if query.created_from is not None else None
            ),
            "created_to": query.created_to.isoformat() if query.created_to is not None else None,
            "outcomes": sorted(outcome.value for outcome in query.outcomes),
            "resource_id": query.resource_id,
            "resource_type": query.resource_type,
        }
    )


def _encode_cursor(position: AuditCursorPosition, *, query_fingerprint: str) -> str:
    payload = _canonical_json(
        {
            "created_at": position.created_at.isoformat(),
            "event_id": position.event_id,
            "query_fingerprint": query_fingerprint,
            "version": _CURSOR_VERSION,
        }
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, *, query_fingerprint: str) -> AuditCursorPosition:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {
            "created_at",
            "event_id",
            "query_fingerprint",
            "version",
        }:
            raise ValueError
        if payload["version"] != _CURSOR_VERSION:
            raise ValueError
        if payload["query_fingerprint"] != query_fingerprint:
            raise ValueError
        created_at_value = payload["created_at"]
        event_id = payload["event_id"]
        if not isinstance(created_at_value, str) or not isinstance(event_id, str):
            raise ValueError
        if _EVENT_ID.fullmatch(event_id) is None:
            raise ValueError
        created_at = datetime.fromisoformat(created_at_value)
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise InvalidAuditCursorError("The Audit cursor is invalid") from error
    return AuditCursorPosition(created_at, event_id)


def _snapshot(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("audit snapshot must be a mapping or None")
    return {"summary": value, "fingerprint": _fingerprint(value)}


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
