"""Final write-time redaction boundary for every persisted Run Event."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC

import aiosqlite

from pangi.application.contracts.run_events import RunEventDraft
from pangi.application.services.telemetry_redaction import TelemetryRedactionService
from pangi.domain.runs import RunEvent


class SqliteRunEventWriter:
    """Sanitize immediately before the shared Run Event INSERT statement."""

    def __init__(self, redactor: TelemetryRedactionService) -> None:
        self._redactor = redactor

    def prepare_draft(self, draft: RunEventDraft, *, index: int) -> RunEvent:
        safe = self._redactor.sanitize_event(
            draft.message,
            {} if draft.attributes is None else draft.attributes,
        )
        return replace(
            draft,
            message=safe.message,
            attributes=safe.attributes,
        ).to_event(index=index)

    async def insert(
        self,
        connection: aiosqlite.Connection,
        event: RunEvent,
    ) -> RunEvent:
        safe = self._redactor.sanitize_event(event.message, event.attributes)
        prepared = replace(
            event,
            message=safe.message,
            attributes=safe.attributes,
        )
        await connection.execute(
            "INSERT INTO run_events "
            "(run_id, event_index, type, visibility, step_id, message, attributes_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                prepared.run_id,
                prepared.index,
                prepared.type,
                prepared.visibility.value,
                prepared.step_id,
                prepared.message,
                _canonical_json(prepared.attributes),
                prepared.created_at.astimezone(UTC).isoformat(),
            ),
        )
        return prepared


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(nested) for nested in value]
    return value
