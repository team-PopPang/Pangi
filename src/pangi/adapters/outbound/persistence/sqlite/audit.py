"""SQLite final writer and query store for immutable Audit Events."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all
from pangi.application.contracts.audit import AuditEventDraft, AuditStoreQuery
from pangi.application.ports.audit import AuditPersistenceError
from pangi.application.services.audit import AuditRedactionService
from pangi.domain.audit import AuditContractError, AuditEvent, AuditOutcome

if TYPE_CHECKING:
    from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase

IdFactory = Callable[[], str]


def _identifier() -> str:
    return uuid.uuid4().hex


class SqliteAuditWriter:
    """Sanitize immediately before the single shared Audit INSERT statement."""

    def __init__(
        self,
        redactor: AuditRedactionService,
        *,
        id_factory: IdFactory = _identifier,
    ) -> None:
        self._redactor = redactor
        self._id_factory = id_factory

    @property
    def retention_days(self) -> int:
        return self._redactor.policy.retention_days

    def prepare(self, draft: AuditEventDraft) -> AuditEvent:
        return self._redactor.prepare(draft, event_id=self._id_factory())

    async def insert(
        self,
        connection: aiosqlite.Connection,
        draft: AuditEventDraft,
    ) -> AuditEvent:
        event = self.prepare(draft)
        await connection.execute(
            "INSERT INTO audit_events "
            "(id, actor_id, action, resource_type, resource_id, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.actor_id,
                event.action,
                event.resource_type,
                event.resource_id,
                _canonical_json(event.metadata),
                event.created_at.isoformat(),
            ),
        )
        return event


class SqliteAuditStore:
    """Own standalone Audit writes, filtered reads, and expired retention purges."""

    def __init__(self, database: SqliteDatabase, writer: SqliteAuditWriter) -> None:
        self._database = database
        self._writer = writer

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

    async def append_event(self, draft: AuditEventDraft) -> AuditEvent:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                event = await self._writer.insert(unit_of_work.connection, draft)
                await unit_of_work.commit()
                return event
        except aiosqlite.Error as error:
            raise AuditPersistenceError("Audit Event could not be persisted") from error

    async def list_events(self, query: AuditStoreQuery) -> tuple[AuditEvent, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        if query.actor_id is not None:
            clauses.append("actor_id = ?")
            parameters.append(query.actor_id)
        if query.actions:
            clauses.append("action IN (" + ",".join("?" for _ in query.actions) + ")")
            parameters.extend(query.actions)
        if query.resource_type is not None:
            clauses.append("resource_type = ?")
            parameters.append(query.resource_type)
        if query.resource_id is not None:
            clauses.append("resource_id = ?")
            parameters.append(query.resource_id)
        if query.outcomes:
            clauses.append(
                "json_extract(metadata_json, '$.outcome') IN ("
                + ",".join("?" for _ in query.outcomes)
                + ")"
            )
            parameters.extend(outcome.value for outcome in query.outcomes)
        if query.created_from is not None:
            clauses.append("created_at >= ?")
            parameters.append(query.created_from.astimezone(UTC).isoformat())
        if query.created_to is not None:
            clauses.append("created_at <= ?")
            parameters.append(query.created_to.astimezone(UTC).isoformat())
        if query.after is not None:
            clauses.append("(created_at < ? OR (created_at = ? AND id < ?))")
            after_time = query.after.created_at.astimezone(UTC).isoformat()
            parameters.extend((after_time, after_time, query.after.event_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(query.limit)
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT id, actor_id, action, resource_type, resource_id, "
                    "metadata_json, created_at FROM audit_events"
                    + where
                    + " ORDER BY created_at DESC, id DESC LIMIT ?",
                    tuple(parameters),
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise AuditPersistenceError("Audit Events could not be read") from error
        try:
            return tuple(_event(row) for row in rows)
        except (AuditContractError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise AuditPersistenceError("Persisted Audit Event is invalid") from error

    async def purge_expired(self, *, before: datetime, limit: int) -> int:
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("Audit purge cutoff must be timezone-aware")
        if not 1 <= limit <= 10_000:
            raise ValueError("Audit purge limit must be between 1 and 10000")
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                cursor = await unit_of_work.connection.execute(
                    "DELETE FROM audit_events WHERE id IN ("
                    "SELECT id FROM audit_events WHERE created_at <= ? "
                    "ORDER BY created_at, id LIMIT ?)",
                    (before.astimezone(UTC).isoformat(), limit),
                )
                try:
                    removed = cursor.rowcount
                finally:
                    await cursor.close()
                await unit_of_work.commit()
                return max(0, removed)
        except aiosqlite.Error as error:
            raise AuditPersistenceError("Expired Audit Events could not be purged") from error


def _event(row: aiosqlite.Row) -> AuditEvent:
    metadata = json.loads(str(row["metadata_json"]))
    if not isinstance(metadata, dict):
        raise TypeError("audit metadata must be a mapping")
    return AuditEvent(
        id=str(row["id"]),
        actor_id=str(row["actor_id"]),
        action=str(row["action"]),
        resource_type=str(row["resource_type"]),
        resource_id=str(row["resource_id"]),
        outcome=AuditOutcome(str(metadata.get("outcome"))),
        metadata=metadata,
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


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
