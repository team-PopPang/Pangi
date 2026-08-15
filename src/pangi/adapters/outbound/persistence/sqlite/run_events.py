"""SQLite persistence for Run Events and identifier-free Queue metrics."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all, fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.application.contracts.run_events import (
    RunEventDraft,
    RunEventStoreBatch,
    RunQueueMetrics,
)
from pangi.application.ports.run_events import (
    RunEventNotFoundError,
    RunEventPersistenceError,
    RunQueueMetricPersistenceError,
)
from pangi.domain.runs import EventVisibility, RunEvent, RunState


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(nested) for nested in value]
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _required_datetime(row: aiosqlite.Row, name: str) -> datetime:
    value = datetime.fromisoformat(str(row[name]))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} is not timezone-aware")
    return value.astimezone(UTC)


def _event_from_row(row: aiosqlite.Row) -> RunEvent:
    try:
        attributes = json.loads(str(row["attributes_json"]))
        if not isinstance(attributes, dict):
            raise ValueError("attributes_json must be an object")
        return RunEvent(
            run_id=str(row["run_id"]),
            index=int(row["event_index"]),
            type=str(row["type"]),
            visibility=EventVisibility(str(row["visibility"])),
            step_id=None if row["step_id"] is None else str(row["step_id"]),
            message=None if row["message"] is None else str(row["message"]),
            attributes=attributes,
            created_at=_required_datetime(row, "created_at"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RunEventPersistenceError("Persisted Run Event data is invalid") from error


class SqliteRunEventStore:
    """Assign Event indexes and execute short, serialized SQLite reads."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    @asynccontextmanager
    async def _runtime(self) -> AsyncIterator[None]:
        started_here = not self._database.started
        if started_here:
            await self._database.start()
        try:
            yield
        finally:
            if started_here:
                await self._database.close()

    async def append_event(self, draft: RunEventDraft) -> RunEvent:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                run = await fetch_one(
                    unit_of_work.connection,
                    "SELECT id FROM runs WHERE id = ?",
                    (draft.run_id,),
                )
                if run is None:
                    raise RunEventNotFoundError("The Run was not found")
                next_index = await fetch_one(
                    unit_of_work.connection,
                    "SELECT COALESCE(MAX(event_index), 0) + 1 AS value "
                    "FROM run_events WHERE run_id = ?",
                    (draft.run_id,),
                )
                if next_index is None:
                    raise RunEventPersistenceError(
                        "The next Run Event index is unavailable"
                    )
                event = draft.to_event(index=int(next_index["value"]))
                await unit_of_work.connection.execute(
                    "INSERT INTO run_events "
                    "(run_id, event_index, type, visibility, step_id, message, "
                    "attributes_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.run_id,
                        event.index,
                        event.type,
                        event.visibility.value,
                        event.step_id,
                        event.message,
                        _canonical_json(event.attributes),
                        event.created_at.astimezone(UTC).isoformat(),
                    ),
                )
                await unit_of_work.commit()
                return event
        except aiosqlite.Error as error:
            raise RunEventPersistenceError("The Run Event store is unavailable") from error

    async def read_events(
        self,
        *,
        run_id: str,
        owner_user_id: str | None,
        visibilities: tuple[EventVisibility, ...],
        after_index: int,
        limit: int,
    ) -> RunEventStoreBatch | None:
        visibility_values = tuple(visibility.value for visibility in visibilities)
        if not visibility_values:
            raise ValueError("at least one Event visibility is required")
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                run_statement = "SELECT state FROM runs WHERE id = ?"
                run_parameters: tuple[object, ...] = (run_id,)
                if owner_user_id is not None:
                    run_statement += " AND principal_id = ?"
                    run_parameters += (owner_user_id,)
                run = await fetch_one(
                    unit_of_work.connection,
                    run_statement,
                    run_parameters,
                )
                if run is None:
                    await unit_of_work.commit()
                    return None
                placeholders = ", ".join("?" for _visibility in visibility_values)
                rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT run_id, event_index, type, visibility, step_id, message, "
                    "attributes_json, created_at FROM run_events "
                    f"WHERE run_id = ? AND event_index > ? AND visibility IN ({placeholders}) "
                    "ORDER BY event_index LIMIT ?",
                    (run_id, after_index, *visibility_values, limit),
                )
                await unit_of_work.commit()
                state = RunState(str(run["state"]))
        except (TypeError, ValueError) as error:
            raise RunEventPersistenceError("Persisted Run Event state is invalid") from error
        except aiosqlite.Error as error:
            raise RunEventPersistenceError("The Run Event store is unavailable") from error
        return RunEventStoreBatch(
            items=tuple(_event_from_row(row) for row in rows),
            run_state=state,
        )

    async def queue_metrics(self, *, at: datetime) -> RunQueueMetrics:
        timestamp = at.astimezone(UTC)
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                row = await fetch_one(
                    unit_of_work.connection,
                    "SELECT "
                    "SUM(CASE WHEN state = 'queued' THEN 1 ELSE 0 END) AS queue_depth, "
                    "SUM(CASE WHEN state = 'running' THEN 1 ELSE 0 END) AS running_count, "
                    "SUM(CASE WHEN state = 'running' "
                    "AND julianday(lease_expires_at) <= julianday(?) THEN 1 ELSE 0 END) "
                    "AS expired_lease_count, "
                    "MIN(CASE WHEN state = 'queued' "
                    "THEN COALESCE(queued_at, created_at) END) AS oldest_queued_at "
                    "FROM runs",
                    (timestamp.isoformat(),),
                )
                await unit_of_work.commit()
            if row is None:
                raise RunQueueMetricPersistenceError(
                    "The Run Queue metric snapshot is unavailable"
                )
            oldest = (
                None
                if row["oldest_queued_at"] is None
                else _required_datetime(row, "oldest_queued_at")
            )
            age = None if oldest is None else max(0.0, (timestamp - oldest).total_seconds())
            return RunQueueMetrics(
                queue_depth=int(row["queue_depth"] or 0),
                running_count=int(row["running_count"] or 0),
                expired_lease_count=int(row["expired_lease_count"] or 0),
                oldest_queued_at=oldest,
                oldest_queued_age_seconds=age,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RunQueueMetricPersistenceError(
                "Persisted Run Queue metric data is invalid"
            ) from error
        except aiosqlite.Error as error:
            raise RunQueueMetricPersistenceError(
                "The Run Queue metric store is unavailable"
            ) from error
