"""SQLite persistence for governed Tool Invocation lifecycles."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.event_writer import SqliteRunEventWriter
from pangi.application.contracts.run_events import RunEventDraft
from pangi.application.contracts.tool_invocation_persistence import (
    ToolInvocationDenial,
    ToolInvocationFinish,
    ToolInvocationStart,
)
from pangi.application.ports.tool_invocation_persistence import (
    ToolInvocationPersistenceError,
)
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)
from pangi.domain.runs import EventVisibility
from pangi.domain.tool_guardrails import ToolInvocationState


class SqliteToolInvocationRecorder:
    """Commit every state change with one secret-safe internal Run Event."""

    def __init__(
        self,
        database: SqliteDatabase,
        event_writer: SqliteRunEventWriter | None = None,
    ) -> None:
        self._database = database
        self._event_writer = event_writer or SqliteRunEventWriter(
            core_telemetry_redaction_service()
        )

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

    async def start(self, invocation: ToolInvocationStart) -> None:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "INSERT INTO tool_invocations "
                    "(id, run_id, step_id, connection_id, stable_tool_id, policy_version, "
                    "policy_fingerprint, approval_grant_id, arguments_fingerprint, "
                    "argument_bytes, permission, calls_used, timeout_seconds, "
                    "max_result_bytes, duration_ms, state, error_code, created_at, "
                    "finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, "
                    "'running', NULL, ?, NULL)",
                    (
                        invocation.invocation_id,
                        invocation.context.run_id,
                        invocation.context.step_id,
                        invocation.connection_id,
                        invocation.stable_tool_id,
                        invocation.policy_version,
                        invocation.policy_fingerprint,
                        invocation.approval_grant_id,
                        invocation.arguments_fingerprint,
                        invocation.argument_bytes,
                        invocation.permission.value,
                        invocation.calls_used,
                        invocation.timeout_seconds,
                        invocation.max_result_bytes,
                        invocation.started_at.isoformat(),
                    ),
                )
                await self._append_event(
                    unit_of_work.connection,
                    RunEventDraft(
                        run_id=invocation.context.run_id,
                        step_id=invocation.context.step_id,
                        type="tool.invocation_started",
                        visibility=EventVisibility.INTERNAL,
                        created_at=invocation.started_at,
                        message="Tool invocation started after governance checks",
                        attributes=_start_attributes(invocation),
                    ),
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise ToolInvocationPersistenceError(
                "Tool Invocation could not be started"
            ) from error

    async def deny(self, invocation: ToolInvocationDenial) -> None:
        decision = invocation.decision
        error_code = decision.error_code
        assert error_code is not None
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                existing = await fetch_one(
                    unit_of_work.connection,
                    "SELECT * FROM tool_invocations WHERE id = ?",
                    (invocation.invocation_id,),
                )
                if existing is not None:
                    if not _same_denial(existing, invocation):
                        raise ToolInvocationPersistenceError(
                            "Tool Invocation denial conflicts with persisted state"
                        )
                    await unit_of_work.commit()
                    return
                await unit_of_work.connection.execute(
                    "INSERT INTO tool_invocations "
                    "(id, run_id, step_id, connection_id, stable_tool_id, policy_version, "
                    "policy_fingerprint, approval_grant_id, arguments_fingerprint, "
                    "argument_bytes, permission, calls_used, timeout_seconds, "
                    "max_result_bytes, duration_ms, state, error_code, created_at, "
                    "finished_at) VALUES (?, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?, ?, ?, "
                    "NULL, NULL, 0, 'denied', ?, ?, ?)",
                    (
                        invocation.invocation_id,
                        invocation.context.run_id,
                        invocation.context.step_id,
                        decision.tool_id,
                        decision.policy_version,
                        decision.policy_fingerprint,
                        decision.argument_bytes,
                        None if decision.permission is None else decision.permission.value,
                        decision.calls_used,
                        error_code.value,
                        invocation.denied_at.isoformat(),
                        invocation.denied_at.isoformat(),
                    ),
                )
                await self._append_event(
                    unit_of_work.connection,
                    RunEventDraft(
                        run_id=invocation.context.run_id,
                        step_id=invocation.context.step_id,
                        type="tool.invocation_denied",
                        visibility=EventVisibility.INTERNAL,
                        created_at=invocation.denied_at,
                        message="Tool invocation was denied before external execution",
                        attributes={"decision": decision.as_dict()},
                    ),
                )
                await unit_of_work.commit()
        except ToolInvocationPersistenceError:
            raise
        except aiosqlite.Error as error:
            raise ToolInvocationPersistenceError(
                "Denied Tool Invocation could not be persisted"
            ) from error

    async def finish(self, invocation: ToolInvocationFinish) -> None:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                row = await fetch_one(
                    unit_of_work.connection,
                    "SELECT run_id, step_id, state, duration_ms, error_code, finished_at "
                    "FROM tool_invocations WHERE id = ?",
                    (invocation.invocation_id,),
                )
                if row is None:
                    raise ToolInvocationPersistenceError(
                        "Tool Invocation is missing or already terminal"
                    )
                if str(row["state"]) != ToolInvocationState.RUNNING.value:
                    if not _same_finish(row, invocation):
                        raise ToolInvocationPersistenceError(
                            "Tool Invocation terminal state conflicts with persisted state"
                        )
                    await unit_of_work.commit()
                    return
                cursor = await unit_of_work.connection.execute(
                    "UPDATE tool_invocations SET state = ?, duration_ms = ?, error_code = ?, "
                    "finished_at = ? WHERE id = ? AND state = 'running'",
                    (
                        invocation.state.value,
                        invocation.duration_ms,
                        (
                            None
                            if invocation.error_code is None
                            else invocation.error_code.value
                        ),
                        invocation.finished_at.isoformat(),
                        invocation.invocation_id,
                    ),
                )
                try:
                    if cursor.rowcount != 1:
                        raise ToolInvocationPersistenceError(
                            "Tool Invocation is missing or already terminal"
                        )
                finally:
                    await cursor.close()
                await self._append_event(
                    unit_of_work.connection,
                    RunEventDraft(
                        run_id=str(row["run_id"]),
                        step_id=None if row["step_id"] is None else str(row["step_id"]),
                        type="tool.invocation_finished",
                        visibility=EventVisibility.INTERNAL,
                        created_at=invocation.finished_at,
                        message="Tool invocation reached a terminal state",
                        attributes=_finish_attributes(invocation),
                    ),
                )
                await unit_of_work.commit()
        except ToolInvocationPersistenceError:
            raise
        except aiosqlite.Error as error:
            raise ToolInvocationPersistenceError(
                "Tool Invocation could not be finalized"
            ) from error

    async def _append_event(
        self,
        connection: aiosqlite.Connection,
        draft: RunEventDraft,
    ) -> None:
        row = await fetch_one(
            connection,
            "SELECT COALESCE(MAX(event_index), 0) + 1 AS value "
            "FROM run_events WHERE run_id = ?",
            (draft.run_id,),
        )
        if row is None:
            raise ToolInvocationPersistenceError(
                "The next Tool Run Event index is unavailable"
            )
        event = self._event_writer.prepare_draft(draft, index=int(row["value"]))
        await self._event_writer.insert(connection, event)


def _start_attributes(invocation: ToolInvocationStart) -> dict[str, object]:
    return {
        "argument_bytes": invocation.argument_bytes,
        "arguments_fingerprint": invocation.arguments_fingerprint,
        "calls_used": invocation.calls_used,
        "max_result_bytes": invocation.max_result_bytes,
        "permission": invocation.permission.value,
        "policy_fingerprint": invocation.policy_fingerprint,
        "policy_version": invocation.policy_version,
        "stable_tool_id": invocation.stable_tool_id,
        "state": ToolInvocationState.RUNNING.value,
        "timeout_seconds": invocation.timeout_seconds,
    }


def _finish_attributes(invocation: ToolInvocationFinish) -> dict[str, object]:
    return {
        "duration_ms": invocation.duration_ms,
        "error_code": (
            None if invocation.error_code is None else invocation.error_code.value
        ),
        "state": invocation.state.value,
    }


def _same_denial(row: aiosqlite.Row, invocation: ToolInvocationDenial) -> bool:
    decision = invocation.decision
    error_code = decision.error_code
    assert error_code is not None
    return (
        str(row["run_id"]) == invocation.context.run_id
        and (None if row["step_id"] is None else str(row["step_id"]))
        == invocation.context.step_id
        and str(row["stable_tool_id"]) == decision.tool_id
        and (None if row["policy_version"] is None else str(row["policy_version"]))
        == decision.policy_version
        and (
            None
            if row["policy_fingerprint"] is None
            else str(row["policy_fingerprint"])
        )
        == decision.policy_fingerprint
        and (None if row["argument_bytes"] is None else int(row["argument_bytes"]))
        == decision.argument_bytes
        and (None if row["permission"] is None else str(row["permission"]))
        == (None if decision.permission is None else decision.permission.value)
        and (None if row["calls_used"] is None else int(row["calls_used"]))
        == decision.calls_used
        and str(row["state"]) == ToolInvocationState.DENIED.value
        and str(row["error_code"]) == error_code.value
        and str(row["created_at"]) == invocation.denied_at.isoformat()
        and str(row["finished_at"]) == invocation.denied_at.isoformat()
    )


def _same_finish(row: aiosqlite.Row, invocation: ToolInvocationFinish) -> bool:
    persisted_error = None if row["error_code"] is None else str(row["error_code"])
    expected_error = (
        None if invocation.error_code is None else invocation.error_code.value
    )
    return (
        str(row["state"]) == invocation.state.value
        and int(row["duration_ms"]) == invocation.duration_ms
        and persisted_error == expected_error
        and str(row["finished_at"]) == invocation.finished_at.isoformat()
    )
