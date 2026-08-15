"""Audit sanitization, fingerprint, authorization, and cursor tests."""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from pangi.application.contracts.audit import (
    AuditEventDraft,
    AuditListQuery,
    AuditRedactionError,
    AuditRedactionErrorCode,
    AuditStoreQuery,
)
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.ports.audit import InvalidAuditCursorError
from pangi.application.ports.auth import PermissionDeniedError
from pangi.application.services.audit import (
    AuditQueryService,
    AuditRedactionService,
    core_audit_policy,
    core_audit_redaction_service,
)
from pangi.application.services.redaction import RedactionService, core_secret_redaction_policy
from pangi.domain.audit import AuditEvent, AuditOutcome
from pangi.domain.auth import UserRole, UserStatus

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _draft(**changes: object) -> AuditEventDraft:
    values: dict[str, object] = {
        "actor_id": "admin-user-000001",
        "action": "tool_policy.updated",
        "resource_type": "tool_policy",
        "resource_id": "policy-identifier-001",
        "outcome": AuditOutcome.SUCCEEDED,
        "created_at": NOW,
        "before_summary": {"state": "draft", "token": "before-secret"},
        "after_summary": {
            "state": "active",
            "note": "authorization=Bearer secret-value",
        },
        "details": {"request_id": "request-identifier-01"},
    }
    values.update(changes)
    return AuditEventDraft(**values)  # type: ignore[arg-type]


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def test_audit_redaction_is_deterministic_and_hides_raw_values() -> None:
    service = core_audit_redaction_service()

    first = service.prepare(_draft(), event_id="audit-event-identifier-01")
    second = service.prepare(_draft(), event_id="audit-event-identifier-02")

    first_metadata = _json_value(first.metadata)
    second_metadata = _json_value(second.metadata)
    assert first_metadata == second_metadata
    encoded = json.dumps(first_metadata, ensure_ascii=False, sort_keys=True)
    assert "before-secret" not in encoded
    assert "secret-value" not in encoded
    assert "[REDACTED]" in encoded
    assert len(str(first.metadata["change_fingerprint"])) == 64
    assert "before-secret" not in repr(first)
    assert "secret-value" not in repr(first)


def test_audit_redaction_rejects_cycles_and_oversized_metadata() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    service = core_audit_redaction_service()

    with pytest.raises(AuditRedactionError) as cycle_error:
        service.prepare(
            _draft(details=cyclic),
            event_id="audit-event-identifier-01",
        )
    assert cycle_error.value.code is AuditRedactionErrorCode.INPUT_CYCLE

    small = AuditRedactionService(
        replace(core_audit_policy(), max_metadata_bytes=128),
        RedactionService(core_secret_redaction_policy()),
    )
    with pytest.raises(AuditRedactionError) as size_error:
        small.prepare(
            _draft(details={"value": "x" * 512}),
            event_id="audit-event-identifier-02",
        )
    assert size_error.value.code is AuditRedactionErrorCode.INPUT_TOO_LARGE


class RecordingAuditStore:
    def __init__(self, events: tuple[AuditEvent, ...]) -> None:
        self.events = events
        self.queries: list[AuditStoreQuery] = []

    async def list_events(self, query: AuditStoreQuery) -> tuple[AuditEvent, ...]:
        self.queries.append(query)
        if query.after is None:
            return self.events[: query.limit]
        return tuple(
            event
            for event in self.events
            if (event.created_at, event.id) < (query.after.created_at, query.after.event_id)
        )[: query.limit]


def _principal(role: UserRole, status: UserStatus = UserStatus.ACTIVE) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        "admin-user-000001" if role is UserRole.ADMIN else "member-user-00001",
        "Actor",
        role,
        status,
    )


def test_admin_query_uses_filter_bound_keyset_cursor() -> None:
    redactor = core_audit_redaction_service()
    events = tuple(
        redactor.prepare(
            _draft(created_at=NOW - timedelta(seconds=index)),
            event_id=f"audit-event-identifier-{index:02d}",
        )
        for index in range(3)
    )
    store = RecordingAuditStore(events)
    service = AuditQueryService(store)
    query = AuditListQuery(actions=("tool_policy.updated",), limit=2)

    first = asyncio.run(
        service.list_events(actor=_principal(UserRole.ADMIN), query=query)
    )
    assert len(first.items) == 2
    assert first.next_cursor is not None

    second = asyncio.run(
        service.list_events(
            actor=_principal(UserRole.ADMIN),
            query=replace(query, cursor=first.next_cursor),
        )
    )
    assert [event.id for event in second.items] == [events[2].id]
    assert store.queries[-1].after is not None

    with pytest.raises(InvalidAuditCursorError):
        asyncio.run(
            service.list_events(
                actor=_principal(UserRole.ADMIN),
                query=replace(
                    query,
                    actions=("bootstrap.admin_created",),
                    cursor=first.next_cursor,
                ),
            )
        )


@pytest.mark.parametrize(
    ("role", "status"),
    [
        (UserRole.MEMBER, UserStatus.ACTIVE),
        (UserRole.SKILL_AUTHOR, UserStatus.ACTIVE),
        (UserRole.SYSTEM, UserStatus.ACTIVE),
        (UserRole.ADMIN, UserStatus.DISABLED),
    ],
)
def test_audit_query_rejects_non_admin_or_inactive_principals(
    role: UserRole,
    status: UserStatus,
) -> None:
    service = AuditQueryService(RecordingAuditStore(()))

    with pytest.raises(PermissionDeniedError):
        asyncio.run(
            service.list_events(
                actor=_principal(role, status),
                query=AuditListQuery(),
            )
        )
