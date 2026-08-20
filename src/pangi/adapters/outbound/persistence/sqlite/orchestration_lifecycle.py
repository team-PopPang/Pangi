"""SQLite persistence for Root planning Events and final SafeOutput values."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.event_writer import SqliteRunEventWriter
from pangi.application.contracts.orchestration_lifecycle import (
    OrchestrationDecisionRecord,
    OrchestrationFailureRecord,
    OrchestrationPlanningToken,
)
from pangi.application.contracts.output_guardrails import SafeOutput
from pangi.application.contracts.run_queue import RunClaim
from pangi.application.ports.orchestration_lifecycle import (
    OrchestrationLifecycleConflictError,
    OrchestrationLifecycleError,
    OrchestrationLifecycleNotFoundError,
    OrchestrationLifecyclePersistenceError,
)
from pangi.domain.runs import EventVisibility, RunEvent

_OUTPUT_SCHEMA_VERSION = "orchestration-output-v1"
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,119}$")


class SqliteOrchestrationLifecycleStore:
    def __init__(
        self,
        database: SqliteDatabase,
        event_writer: SqliteRunEventWriter,
    ) -> None:
        self._database = database
        self._event_writer = event_writer

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

    async def start_planning(
        self,
        *,
        run_id: str,
        expected_revision: int,
        at: datetime,
    ) -> OrchestrationPlanningToken:
        timestamp = _utc(at)
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                row = await fetch_one(
                    unit_of_work.connection,
                    "SELECT state, revision FROM runs WHERE id = ?",
                    (run_id,),
                )
                if row is None:
                    raise OrchestrationLifecycleNotFoundError("The orchestration Run was not found")
                if str(row["state"]) != "received" or int(row["revision"]) != expected_revision:
                    raise OrchestrationLifecycleConflictError(
                        "The Run cannot begin orchestration planning"
                    )
                cursor = await unit_of_work.connection.execute(
                    "UPDATE runs SET state = 'planning', revision = revision + 1, "
                    "updated_at = ? WHERE id = ? AND state = 'received' AND revision = ?",
                    (timestamp.isoformat(), run_id, expected_revision),
                )
                try:
                    if cursor.rowcount != 1:
                        raise OrchestrationLifecycleConflictError(
                            "The Run changed before planning started"
                        )
                finally:
                    await cursor.close()
                await self._append_event(
                    unit_of_work.connection,
                    run_id=run_id,
                    event_type="run.planning",
                    visibility=EventVisibility.PUBLIC,
                    at=timestamp,
                    message="Run planning started",
                    attributes={},
                )
                await self._append_event(
                    unit_of_work.connection,
                    run_id=run_id,
                    event_type="orchestrator.started",
                    visibility=EventVisibility.INTERNAL,
                    at=timestamp,
                    message="Root orchestration started",
                    attributes={},
                )
                await unit_of_work.commit()
                return OrchestrationPlanningToken(run_id, expected_revision + 1)
        except OrchestrationLifecycleError:
            raise
        except (aiosqlite.Error, KeyError, TypeError, ValueError) as error:
            raise OrchestrationLifecyclePersistenceError(
                "Orchestration planning could not start"
            ) from error

    async def record_decision(
        self,
        *,
        token: OrchestrationPlanningToken,
        record: OrchestrationDecisionRecord,
        at: datetime,
    ) -> None:
        timestamp = _utc(at)
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._require_planning(unit_of_work.connection, token)
                await self._append_event(
                    unit_of_work.connection,
                    run_id=token.run_id,
                    event_type="orchestrator.decided",
                    visibility=EventVisibility.INTERNAL,
                    at=timestamp,
                    message="Root orchestration decision accepted",
                    attributes={
                        "logical_call_count": record.logical_call_count,
                        "mode": record.mode.value,
                        "plan_fingerprint": record.plan_fingerprint,
                        "provider_request_count": record.provider_request_count,
                    },
                )
                await unit_of_work.commit()
        except OrchestrationLifecycleError:
            raise
        except (aiosqlite.Error, KeyError, TypeError, ValueError) as error:
            raise OrchestrationLifecyclePersistenceError(
                "Orchestration decision metadata could not be recorded"
            ) from error

    async def fail_planning(
        self,
        *,
        token: OrchestrationPlanningToken,
        failure: OrchestrationFailureRecord,
        at: datetime,
    ) -> None:
        timestamp = _utc(at)
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._require_planning(unit_of_work.connection, token)
                cursor = await unit_of_work.connection.execute(
                    "UPDATE runs SET state = 'failed', revision = revision + 1, "
                    "worker_id = NULL, lease_expires_at = NULL, heartbeat_at = NULL, "
                    "error_code = ?, updated_at = ?, finished_at = ? "
                    "WHERE id = ? AND state = 'planning' AND revision = ?",
                    (
                        failure.error_code,
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                        token.run_id,
                        token.revision,
                    ),
                )
                try:
                    if cursor.rowcount != 1:
                        raise OrchestrationLifecycleConflictError(
                            "The planning Run changed before failure was recorded"
                        )
                finally:
                    await cursor.close()
                await self._append_event(
                    unit_of_work.connection,
                    run_id=token.run_id,
                    event_type="orchestrator.failed",
                    visibility=EventVisibility.INTERNAL,
                    at=timestamp,
                    message="Root orchestration failed",
                    attributes={
                        "error_code": failure.error_code,
                        "logical_call_count": failure.logical_call_count,
                        "provider_request_count": failure.provider_request_count,
                    },
                )
                await self._append_event(
                    unit_of_work.connection,
                    run_id=token.run_id,
                    event_type="run.failed",
                    visibility=EventVisibility.PUBLIC,
                    at=timestamp,
                    message="Run planning failed",
                    attributes={"error_code": failure.error_code},
                )
                await unit_of_work.commit()
        except OrchestrationLifecycleError:
            raise
        except (aiosqlite.Error, KeyError, TypeError, ValueError) as error:
            raise OrchestrationLifecyclePersistenceError(
                "Orchestration planning failure could not be recorded"
            ) from error

    async def complete_output(
        self,
        *,
        claim: RunClaim,
        output: SafeOutput,
        at: datetime,
    ) -> None:
        if not isinstance(output, SafeOutput):
            raise TypeError("output must be SafeOutput")
        timestamp = _utc(at)
        evidence_links_json = _canonical_json(list(output.evidence_links))
        guardrail_metadata_json = _canonical_json(output.as_metadata())
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._require_composing_owner(
                    unit_of_work.connection,
                    claim=claim,
                    at=timestamp,
                )
                await unit_of_work.connection.execute(
                    "INSERT INTO run_outputs "
                    "(run_id, schema_version, markdown, evidence_links_json, "
                    "content_fingerprint, guardrail_metadata_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        claim.run_id,
                        _OUTPUT_SCHEMA_VERSION,
                        output.markdown,
                        evidence_links_json,
                        output.content_fingerprint,
                        guardrail_metadata_json,
                        timestamp.isoformat(),
                    ),
                )
                cursor = await unit_of_work.connection.execute(
                    "UPDATE runs SET state = 'completed', revision = revision + 1, "
                    "worker_id = NULL, lease_expires_at = NULL, heartbeat_at = NULL, "
                    "updated_at = ?, finished_at = ? "
                    "WHERE id = ? AND state = 'composing' AND worker_id = ? "
                    "AND julianday(lease_expires_at) > julianday(?)",
                    (
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                        claim.run_id,
                        claim.worker_id,
                        timestamp.isoformat(),
                    ),
                )
                try:
                    if cursor.rowcount != 1:
                        raise OrchestrationLifecycleConflictError(
                            "The composing Run changed before Output completion"
                        )
                finally:
                    await cursor.close()
                if output.summary.changed:
                    await self._append_event(
                        unit_of_work.connection,
                        run_id=claim.run_id,
                        event_type="output.redacted",
                        visibility=EventVisibility.INTERNAL,
                        at=timestamp,
                        message="Output guardrail changed proposed content",
                        attributes=output.summary.as_dict(),
                    )
                await self._append_event(
                    unit_of_work.connection,
                    run_id=claim.run_id,
                    event_type="output.completed",
                    visibility=EventVisibility.INTERNAL,
                    at=timestamp,
                    message="Safe Output persisted",
                    attributes={
                        "content_fingerprint": output.content_fingerprint,
                        "evidence_link_count": len(output.evidence_links),
                        "output_bytes": output.summary.output_bytes,
                    },
                )
                await self._append_event(
                    unit_of_work.connection,
                    run_id=claim.run_id,
                    event_type="run.completed",
                    visibility=EventVisibility.PUBLIC,
                    at=timestamp,
                    message="Run completed",
                    attributes={
                        "evidence_link_count": len(output.evidence_links),
                        "warning_count": await self._warning_count(
                            unit_of_work.connection,
                            claim.run_id,
                        ),
                    },
                )
                await unit_of_work.commit()
        except OrchestrationLifecycleError:
            raise
        except aiosqlite.IntegrityError as error:
            raise OrchestrationLifecycleConflictError(
                "A final Output already exists or violates its contract"
            ) from error
        except (aiosqlite.Error, KeyError, TypeError, ValueError) as error:
            raise OrchestrationLifecyclePersistenceError(
                "Safe Output completion could not be persisted"
            ) from error

    async def fail_composition(
        self,
        *,
        claim: RunClaim,
        error_code: str,
        at: datetime,
    ) -> None:
        if _ERROR_CODE.fullmatch(error_code) is None:
            raise ValueError("error_code must be a stable lowercase identifier")
        timestamp = _utc(at)
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._require_composing_owner(
                    unit_of_work.connection,
                    claim=claim,
                    at=timestamp,
                )
                cursor = await unit_of_work.connection.execute(
                    "UPDATE runs SET state = 'failed', revision = revision + 1, "
                    "worker_id = NULL, lease_expires_at = NULL, heartbeat_at = NULL, "
                    "error_code = ?, updated_at = ?, finished_at = ? "
                    "WHERE id = ? AND state = 'composing' AND worker_id = ? "
                    "AND julianday(lease_expires_at) > julianday(?)",
                    (
                        error_code,
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                        claim.run_id,
                        claim.worker_id,
                        timestamp.isoformat(),
                    ),
                )
                try:
                    if cursor.rowcount != 1:
                        raise OrchestrationLifecycleConflictError(
                            "The composing Run changed before failure was recorded"
                        )
                finally:
                    await cursor.close()
                await self._append_event(
                    unit_of_work.connection,
                    run_id=claim.run_id,
                    event_type="output.failed",
                    visibility=EventVisibility.INTERNAL,
                    at=timestamp,
                    message="Output composition failed",
                    attributes={"error_code": error_code},
                )
                await self._append_event(
                    unit_of_work.connection,
                    run_id=claim.run_id,
                    event_type="run.failed",
                    visibility=EventVisibility.PUBLIC,
                    at=timestamp,
                    message="Run Output failed",
                    attributes={"error_code": error_code},
                )
                await unit_of_work.commit()
        except OrchestrationLifecycleError:
            raise
        except (aiosqlite.Error, KeyError, TypeError, ValueError) as error:
            raise OrchestrationLifecyclePersistenceError(
                "Output composition failure could not be persisted"
            ) from error

    @staticmethod
    async def _require_planning(
        connection: aiosqlite.Connection,
        token: OrchestrationPlanningToken,
    ) -> None:
        row = await fetch_one(
            connection,
            "SELECT 1 FROM runs WHERE id = ? AND state = 'planning' AND revision = ?",
            (token.run_id, token.revision),
        )
        if row is None:
            raise OrchestrationLifecycleConflictError("The orchestration planning token is stale")

    @staticmethod
    async def _require_composing_owner(
        connection: aiosqlite.Connection,
        *,
        claim: RunClaim,
        at: datetime,
    ) -> None:
        row = await fetch_one(
            connection,
            "SELECT 1 FROM runs WHERE id = ? AND state = 'composing' AND worker_id = ? "
            "AND julianday(lease_expires_at) > julianday(?)",
            (claim.run_id, claim.worker_id, at.isoformat()),
        )
        if row is None:
            raise OrchestrationLifecycleConflictError("The composing Run ownership was lost")

    @staticmethod
    async def _warning_count(
        connection: aiosqlite.Connection,
        run_id: str,
    ) -> int:
        row = await fetch_one(
            connection,
            "SELECT json_array_length(warnings_json) AS value FROM runs WHERE id = ?",
            (run_id,),
        )
        if row is None:
            raise OrchestrationLifecycleNotFoundError("The completed Run was not found")
        return int(row["value"])

    async def _append_event(
        self,
        connection: aiosqlite.Connection,
        *,
        run_id: str,
        event_type: str,
        visibility: EventVisibility,
        at: datetime,
        message: str,
        attributes: Mapping[str, object],
    ) -> None:
        row = await fetch_one(
            connection,
            "SELECT COALESCE(MAX(event_index), 0) + 1 AS next_index "
            "FROM run_events WHERE run_id = ?",
            (run_id,),
        )
        if row is None:
            raise OrchestrationLifecyclePersistenceError("The next Run Event index is unavailable")
        await self._event_writer.insert(
            connection,
            RunEvent(
                run_id=run_id,
                index=int(row["next_index"]),
                type=event_type,
                visibility=visibility,
                created_at=at,
                message=message,
                attributes=attributes,
            ),
        )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("orchestration lifecycle timestamp must be timezone-aware")
    return value.astimezone(UTC)
