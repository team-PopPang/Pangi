"""SQLite persistence for Run creation, idempotency, and owner-scoped queries."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all, fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.application.contracts.runs import (
    RunCreateRecord,
    RunCreation,
    RunStoreQuery,
    RunSummary,
)
from pangi.application.ports.runs import (
    IdempotencyConflictError,
    IdempotencyUnavailableError,
    RunPersistenceError,
    RunPrincipalUnavailableError,
    RunRequestConflictError,
)
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.runs import (
    AttachmentRef,
    Principal,
    PrincipalChannel,
    Run,
    RunEvent,
    RunMode,
    RunRequest,
    RunState,
)

_RUN_COLUMNS = """
    r.id,
    r.request_id,
    r.principal_id,
    r.trigger,
    r.state,
    r.mode,
    r.skill_version_id,
    r.request_text,
    r.thread_key,
    r.explicit_skill,
    r.schedule_id,
    r.attachments_json,
    r.idempotency_key,
    r.revision,
    r.worker_id,
    r.lease_expires_at,
    r.heartbeat_at,
    r.warnings_json,
    r.error_code,
    r.created_at,
    r.updated_at,
    r.started_at,
    r.finished_at,
    u.role AS principal_role
"""

_SUMMARY_COLUMNS = """
    r.id,
    r.request_id,
    r.principal_id,
    r.trigger,
    r.state,
    r.mode,
    r.skill_version_id,
    r.revision,
    r.created_at,
    r.updated_at,
    r.started_at,
    r.finished_at,
    json_array_length(r.warnings_json) AS warning_count,
    r.error_code
"""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(nested) for nested in value]
    return value


def _attachments_json(run: Run) -> str:
    return _canonical_json(
        [
            {
                "display_name": attachment.display_name,
                "fingerprint": attachment.fingerprint,
                "media_type": attachment.media_type,
                "reference": attachment.reference,
                "size_bytes": attachment.size_bytes,
            }
            for attachment in run.request.attachments
        ]
    )


def _required_datetime(row: aiosqlite.Row, name: str) -> datetime:
    value = datetime.fromisoformat(str(row[name]))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} is not timezone-aware")
    return value.astimezone(UTC)


def _optional_datetime(row: aiosqlite.Row, name: str) -> datetime | None:
    return None if row[name] is None else _required_datetime(row, name)


def _optional_text(row: aiosqlite.Row, name: str) -> str | None:
    return None if row[name] is None else str(row[name])


def _attachments(value: object) -> tuple[AttachmentRef, ...]:
    parsed = json.loads(str(value))
    if not isinstance(parsed, list):
        raise ValueError("attachments_json must be an array")
    attachments: list[AttachmentRef] = []
    allowed = {"display_name", "fingerprint", "media_type", "reference", "size_bytes"}
    for item in parsed:
        if not isinstance(item, dict) or set(item) != allowed:
            raise ValueError("attachment shape is invalid")
        reference = item["reference"]
        display_name = item["display_name"]
        media_type = item["media_type"]
        size_bytes = item["size_bytes"]
        fingerprint = item["fingerprint"]
        if not isinstance(reference, str):
            raise ValueError("attachment reference is invalid")
        if display_name is not None and not isinstance(display_name, str):
            raise ValueError("attachment display_name is invalid")
        if media_type is not None and not isinstance(media_type, str):
            raise ValueError("attachment media_type is invalid")
        if size_bytes is not None and (
            not isinstance(size_bytes, int) or isinstance(size_bytes, bool)
        ):
            raise ValueError("attachment size_bytes is invalid")
        if fingerprint is not None and not isinstance(fingerprint, str):
            raise ValueError("attachment fingerprint is invalid")
        attachments.append(
            AttachmentRef(
                reference=reference,
                display_name=display_name,
                media_type=media_type,
                size_bytes=size_bytes,
                fingerprint=fingerprint,
            )
        )
    return tuple(attachments)


def _warnings(value: object) -> tuple[str, ...]:
    parsed = json.loads(str(value))
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("warnings_json must be a string array")
    return tuple(parsed)


def _run_from_row(row: aiosqlite.Row) -> Run:
    try:
        request = RunRequest(
            request_id=str(row["request_id"]),
            principal=Principal(
                user_id=str(row["principal_id"]),
                role=UserRole(str(row["principal_role"])),
                channel=PrincipalChannel(str(row["trigger"])),
            ),
            text=str(row["request_text"]),
            idempotency_key=str(row["idempotency_key"]),
            created_at=_required_datetime(row, "created_at"),
            thread_key=_optional_text(row, "thread_key"),
            explicit_skill=_optional_text(row, "explicit_skill"),
            schedule_id=_optional_text(row, "schedule_id"),
            attachments=_attachments(row["attachments_json"]),
        )
        mode_value = _optional_text(row, "mode")
        return Run(
            id=str(row["id"]),
            request=request,
            state=RunState(str(row["state"])),
            mode=RunMode(mode_value) if mode_value is not None else None,
            skill_version_id=_optional_text(row, "skill_version_id"),
            revision=int(row["revision"]),
            worker_id=_optional_text(row, "worker_id"),
            lease_expires_at=_optional_datetime(row, "lease_expires_at"),
            heartbeat_at=_optional_datetime(row, "heartbeat_at"),
            warnings=_warnings(row["warnings_json"]),
            error_code=_optional_text(row, "error_code"),
            updated_at=_required_datetime(row, "updated_at"),
            started_at=_optional_datetime(row, "started_at"),
            finished_at=_optional_datetime(row, "finished_at"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RunPersistenceError("Persisted Run data is invalid") from error


def _summary_from_row(row: aiosqlite.Row) -> RunSummary:
    try:
        mode_value = _optional_text(row, "mode")
        return RunSummary(
            id=str(row["id"]),
            request_id=str(row["request_id"]),
            principal_id=str(row["principal_id"]),
            trigger=PrincipalChannel(str(row["trigger"])),
            state=RunState(str(row["state"])),
            mode=RunMode(mode_value) if mode_value is not None else None,
            skill_version_id=_optional_text(row, "skill_version_id"),
            revision=int(row["revision"]),
            created_at=_required_datetime(row, "created_at"),
            updated_at=_required_datetime(row, "updated_at"),
            started_at=_optional_datetime(row, "started_at"),
            finished_at=_optional_datetime(row, "finished_at"),
            warning_count=int(row["warning_count"]),
            error_code=_optional_text(row, "error_code"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RunPersistenceError("Persisted Run summary is invalid") from error


class SqliteRunStore:
    """Own Run persistence on the runtime's serialized SQLite transaction."""

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

    async def create_or_replay(self, record: RunCreateRecord) -> RunCreation:
        run = record.run
        event = record.first_event
        if event.run_id != run.id or event.index != 1:
            raise RunPersistenceError("The first Run Event contract is invalid")
        recorded_at = record.recorded_at.astimezone(UTC).isoformat()
        expires_at = record.expires_at.astimezone(UTC).isoformat()
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                principal = await fetch_one(
                    unit_of_work.connection,
                    "SELECT role, status FROM users WHERE id = ?",
                    (run.request.principal.user_id,),
                )
                if (
                    principal is None
                    or str(principal["status"]) != UserStatus.ACTIVE.value
                    or str(principal["role"]) != run.request.principal.role.value
                ):
                    raise RunPrincipalUnavailableError(
                        "The Run Principal is unavailable"
                    )

                await unit_of_work.connection.execute(
                    "DELETE FROM api_idempotency_records "
                    "WHERE principal_id = ? AND route_key = ? AND idempotency_key = ? "
                    "AND expires_at <= ?",
                    (
                        run.request.principal.user_id,
                        record.route_key,
                        run.request.idempotency_key,
                        recorded_at,
                    ),
                )
                existing = await fetch_one(
                    unit_of_work.connection,
                    "SELECT request_fingerprint, state, response_json, run_id "
                    "FROM api_idempotency_records "
                    "WHERE principal_id = ? AND route_key = ? AND idempotency_key = ?",
                    (
                        run.request.principal.user_id,
                        record.route_key,
                        run.request.idempotency_key,
                    ),
                )
                if existing is not None:
                    replay = await self._replay_existing(
                        unit_of_work.connection,
                        existing,
                        request_fingerprint=record.request_fingerprint,
                    )
                    await unit_of_work.commit()
                    return RunCreation(replay, True)

                await unit_of_work.connection.execute(
                    "INSERT INTO api_idempotency_records "
                    "(principal_id, route_key, idempotency_key, request_fingerprint, "
                    "response_json, state, run_id, expires_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, NULL, 'processing', NULL, ?, ?, ?)",
                    (
                        run.request.principal.user_id,
                        record.route_key,
                        run.request.idempotency_key,
                        record.request_fingerprint,
                        expires_at,
                        recorded_at,
                        recorded_at,
                    ),
                )
                await self._insert_run(unit_of_work.connection, run)
                await self._insert_event(unit_of_work.connection, event)
                response_json = _canonical_json({"run_id": run.id})
                cursor = await unit_of_work.connection.execute(
                    "UPDATE api_idempotency_records "
                    "SET response_json = ?, state = 'completed', run_id = ?, updated_at = ? "
                    "WHERE principal_id = ? AND route_key = ? AND idempotency_key = ? "
                    "AND state = 'processing'",
                    (
                        response_json,
                        run.id,
                        recorded_at,
                        run.request.principal.user_id,
                        record.route_key,
                        run.request.idempotency_key,
                    ),
                )
                try:
                    if cursor.rowcount != 1:
                        raise RunPersistenceError(
                            "The idempotency result could not be finalized"
                        )
                finally:
                    await cursor.close()
                await unit_of_work.commit()
        except aiosqlite.IntegrityError as error:
            if "runs.request_id" in str(error):
                raise RunRequestConflictError(
                    "The Run request identifier is unavailable"
                ) from error
            raise RunPersistenceError("The Run could not be persisted") from error
        except aiosqlite.Error as error:
            raise RunPersistenceError("The Run store is unavailable") from error
        return RunCreation(run, False)

    async def _replay_existing(
        self,
        connection: aiosqlite.Connection,
        existing: aiosqlite.Row,
        *,
        request_fingerprint: str,
    ) -> Run:
        if str(existing["request_fingerprint"]) != request_fingerprint:
            raise IdempotencyConflictError(
                "The idempotency key was used for a different request"
            )
        if str(existing["state"]) != "completed" or existing["run_id"] is None:
            raise IdempotencyUnavailableError(
                "The idempotent result is not available"
            )
        run_id = str(existing["run_id"])
        try:
            response = json.loads(str(existing["response_json"]))
        except (TypeError, json.JSONDecodeError) as error:
            raise RunPersistenceError("The idempotent response is invalid") from error
        if response != {"run_id": run_id}:
            raise RunPersistenceError("The idempotent response is invalid")
        row = await self._select_run(connection, run_id=run_id, owner_user_id=None)
        if row is None:
            raise RunPersistenceError("The idempotent Run is unavailable")
        return _run_from_row(row)

    @staticmethod
    async def _insert_run(connection: aiosqlite.Connection, run: Run) -> None:
        request = run.request
        await connection.execute(
            "INSERT INTO runs "
            "(id, request_id, principal_id, trigger, state, mode, skill_version_id, "
            "request_text, thread_key, explicit_skill, schedule_id, attachments_json, "
            "idempotency_key, revision, worker_id, lease_expires_at, heartbeat_at, "
            "warnings_json, error_code, created_at, updated_at, queued_at, started_at, "
            "finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?)",
            (
                run.id,
                request.request_id,
                request.principal.user_id,
                request.principal.channel.value,
                run.state.value,
                run.mode.value if run.mode is not None else None,
                run.skill_version_id,
                request.text,
                request.thread_key,
                request.explicit_skill,
                request.schedule_id,
                _attachments_json(run),
                request.idempotency_key,
                run.revision,
                run.worker_id,
                run.lease_expires_at.isoformat() if run.lease_expires_at is not None else None,
                run.heartbeat_at.isoformat() if run.heartbeat_at is not None else None,
                _canonical_json(list(run.warnings)),
                run.error_code,
                request.created_at.astimezone(UTC).isoformat(),
                run.updated_at.astimezone(UTC).isoformat(),
                (
                    run.updated_at.astimezone(UTC).isoformat()
                    if run.state is RunState.QUEUED
                    else None
                ),
                run.started_at.isoformat() if run.started_at is not None else None,
                run.finished_at.isoformat() if run.finished_at is not None else None,
            ),
        )

    @staticmethod
    async def _insert_event(connection: aiosqlite.Connection, event: RunEvent) -> None:
        await connection.execute(
            "INSERT INTO run_events "
            "(run_id, event_index, type, visibility, step_id, message, attributes_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.run_id,
                event.index,
                event.type,
                event.visibility.value,
                event.step_id,
                event.message,
                _canonical_json(_json_value(event.attributes)),
                event.created_at.astimezone(UTC).isoformat(),
            ),
        )

    async def get_run(self, *, run_id: str, owner_user_id: str | None) -> Run | None:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                row = await self._select_run(
                    unit_of_work.connection,
                    run_id=run_id,
                    owner_user_id=owner_user_id,
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise RunPersistenceError("The Run store is unavailable") from error
        return None if row is None else _run_from_row(row)

    @staticmethod
    async def _select_run(
        connection: aiosqlite.Connection,
        *,
        run_id: str,
        owner_user_id: str | None,
    ) -> aiosqlite.Row | None:
        statement = (
            f"SELECT {_RUN_COLUMNS} FROM runs r "
            "JOIN users u ON u.id = r.principal_id WHERE r.id = ?"
        )
        parameters: tuple[object, ...] = (run_id,)
        if owner_user_id is not None:
            statement += " AND r.principal_id = ?"
            parameters += (owner_user_id,)
        return await fetch_one(connection, statement, parameters)

    async def list_run_summaries(self, query: RunStoreQuery) -> tuple[RunSummary, ...]:
        conditions: list[str] = []
        parameters: list[object] = []
        if query.owner_user_id is not None:
            conditions.append("r.principal_id = ?")
            parameters.append(query.owner_user_id)
        if query.states:
            placeholders = ", ".join("?" for _state in query.states)
            conditions.append(f"r.state IN ({placeholders})")
            parameters.extend(state.value for state in query.states)
        if query.triggers:
            placeholders = ", ".join("?" for _trigger in query.triggers)
            conditions.append(f"r.trigger IN ({placeholders})")
            parameters.extend(trigger.value for trigger in query.triggers)
        if query.after is not None:
            conditions.append("(r.created_at < ? OR (r.created_at = ? AND r.id < ?))")
            timestamp = query.after.created_at.astimezone(UTC).isoformat()
            parameters.extend((timestamp, timestamp, query.after.run_id))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        statement = (
            f"SELECT {_SUMMARY_COLUMNS} FROM runs r{where} "
            "ORDER BY r.created_at DESC, r.id DESC LIMIT ?"
        )
        parameters.append(query.limit)
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                rows = await fetch_all(unit_of_work.connection, statement, tuple(parameters))
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise RunPersistenceError("The Run store is unavailable") from error
        return tuple(_summary_from_row(row) for row in rows)
