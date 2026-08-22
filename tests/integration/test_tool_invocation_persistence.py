"""Tool Invocation lifecycle SQLite integration tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all, fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.event_writer import SqliteRunEventWriter
from pangi.adapters.outbound.persistence.sqlite.tool_invocations import (
    SqliteToolInvocationRecorder,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.tool_guardrails import ToolGuardrailDecision
from pangi.application.contracts.tool_invocation_persistence import (
    ToolInvocationContext,
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
from pangi.domain.runs import RunEvent
from pangi.domain.tool_guardrails import (
    ToolGuardrailErrorCode,
    ToolGuardrailOutcome,
    ToolGuardrailStage,
    ToolPermission,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)
RUN_ID = "run-identifier-0001"
STEP_ID = "step-identifier-0001"
CONNECTION_ID = "connection-instance-0001"
TOOL_ID = "linear.issue.create"
POLICY_VERSION = "tool-policy-v1"
POLICY_FINGERPRINT = "a" * 64
ARGUMENTS_FINGERPRINT = "b" * 64
SCHEMA_FINGERPRINT = "c" * 64


def _database(tmp_path: Path) -> SqliteDatabase:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    return SqliteDatabase(paths, config.storage)


async def _seed(database: SqliteDatabase) -> None:
    timestamp = NOW.isoformat()
    active_at = (NOW + timedelta(seconds=1)).isoformat()
    config_json = json.dumps(
        {
            "args": [],
            "command": None,
            "endpoint": "https://mcp.example.test",
            "schema_version": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    async with database.create() as unit_of_work:
        connection = unit_of_work.connection
        await connection.execute(
            "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
            "VALUES ('member-user-00001', 'Tool Test', 'member', 'active', ?, ?)",
            (timestamp, timestamp),
        )
        await connection.execute(
            "INSERT INTO runs "
            "(id, request_id, principal_id, trigger, state, request_text, idempotency_key, "
            "created_at, updated_at) VALUES (?, 'tool-request-0001', "
            "'member-user-00001', 'eval', 'received', 'safe request', 'tool-once', ?, ?)",
            (RUN_ID, timestamp, timestamp),
        )
        await connection.execute(
            "INSERT INTO run_steps "
            "(id, run_id, node_id, type, state, requirement, idempotent, attempt, "
            "created_at, updated_at) VALUES (?, ?, 'tool-node', 'tool', 'running', "
            "'required', 0, 1, ?, ?)",
            (STEP_ID, RUN_ID, timestamp, timestamp),
        )
        await connection.execute(
            "INSERT INTO connections "
            "(id, kind, display_name, display_qualifier, scope, owner_user_id, transport, "
            "auth_type, state, config_json, secret_ref, connected_at, last_checked_at, "
            "last_error_code, revision, created_at, updated_at) VALUES "
            "(?, 'linear', 'Linear', NULL, 'instance', NULL, 'streamable_http', 'none', "
            "'connected', ?, NULL, ?, ?, NULL, 0, ?, ?)",
            (CONNECTION_ID, config_json, timestamp, timestamp, timestamp, timestamp),
        )
        await connection.execute(
            "INSERT INTO connection_tools "
            "(stable_tool_id, connection_id, remote_name, permission, schema_json, "
            "schema_fingerprint, state, discovered_at) VALUES "
            "(?, ?, 'create_issue', 'write', '{\"type\":\"object\"}', ?, 'active', ?)",
            (TOOL_ID, CONNECTION_ID, SCHEMA_FINGERPRINT, timestamp),
        )
        await connection.execute(
            "INSERT INTO tool_policies "
            "(stable_tool_id, connection_id, policy_version, effect, permission, approval, "
            "schema_fingerprint, max_calls_per_run, max_argument_bytes, timeout_seconds, "
            "max_result_bytes, policy_fingerprint, state, created_at, updated_at) VALUES "
            "(?, ?, ?, 'allow', 'write', 'none', ?, 5, 4096, 30, 8192, ?, 'draft', ?, ?)",
            (
                TOOL_ID,
                CONNECTION_ID,
                POLICY_VERSION,
                SCHEMA_FINGERPRINT,
                POLICY_FINGERPRINT,
                timestamp,
                timestamp,
            ),
        )
        await connection.execute(
            "UPDATE tool_policies SET state = 'active', updated_at = ? "
            "WHERE stable_tool_id = ? AND policy_version = ?",
            (active_at, TOOL_ID, POLICY_VERSION),
        )
        await unit_of_work.commit()


def _start(invocation_id: str, calls_used: int) -> ToolInvocationStart:
    return ToolInvocationStart(
        invocation_id=invocation_id,
        context=ToolInvocationContext(RUN_ID, STEP_ID),
        connection_id=CONNECTION_ID,
        stable_tool_id=TOOL_ID,
        policy_version=POLICY_VERSION,
        policy_fingerprint=POLICY_FINGERPRINT,
        approval_grant_id=None,
        arguments_fingerprint=ARGUMENTS_FINGERPRINT,
        argument_bytes=24,
        permission=ToolPermission.WRITE,
        calls_used=calls_used,
        timeout_seconds=30,
        max_result_bytes=8_192,
        started_at=NOW + timedelta(seconds=calls_used + 1),
    )


def _denial(invocation_id: str) -> ToolInvocationDenial:
    return ToolInvocationDenial(
        invocation_id=invocation_id,
        context=ToolInvocationContext(RUN_ID, STEP_ID),
        decision=ToolGuardrailDecision(
            tool_id=TOOL_ID,
            stage=ToolGuardrailStage.POLICY,
            outcome=ToolGuardrailOutcome.BLOCKED,
            error_code=ToolGuardrailErrorCode.POLICY_DENIED,
        ),
        denied_at=NOW + timedelta(seconds=1),
    )


def test_denied_started_terminal_and_replay_states_are_atomic_and_secret_safe(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _seed(database)
            recorder = SqliteToolInvocationRecorder(database)
            denied = _denial("tool-invocation-denied-001")
            await recorder.deny(denied)
            await recorder.deny(denied)

            started = _start("tool-invocation-complete-01", 1)
            finished = ToolInvocationFinish.completed(
                started.invocation_id,
                duration_ms=125,
                finished_at=NOW + timedelta(seconds=3),
            )
            await recorder.start(started)
            await recorder.finish(finished)
            await recorder.finish(finished)

            with pytest.raises(ToolInvocationPersistenceError, match="conflicts"):
                await recorder.finish(
                    ToolInvocationFinish.failed(
                        started.invocation_id,
                        duration_ms=125,
                        finished_at=NOW + timedelta(seconds=3),
                    )
                )

            with pytest.raises(ToolInvocationPersistenceError, match="started"):
                await recorder.start(_start("tool-invocation-duplicate-01", 1))

            async with database.create() as unit_of_work:
                invocations = await fetch_all(
                    unit_of_work.connection,
                    "SELECT * FROM tool_invocations ORDER BY created_at, id",
                )
                events = await fetch_all(
                    unit_of_work.connection,
                    "SELECT type, visibility, attributes_json FROM run_events "
                    "ORDER BY event_index",
                )
                await unit_of_work.commit()

            assert [str(row["state"]) for row in invocations] == [
                "denied",
                "completed",
            ]
            assert int(invocations[1]["duration_ms"]) == 125
            assert [str(row["type"]) for row in events] == [
                "tool.invocation_denied",
                "tool.invocation_started",
                "tool.invocation_finished",
            ]
            assert {str(row["visibility"]) for row in events} == {"internal"}
            persisted = "\n".join(
                str(value) for row in (*invocations, *events) for value in tuple(row)
            )
            for raw in (
                "raw-tool-arguments-secret",
                "raw-tool-result-secret",
                "raw-approval-reference-secret",
                "raw-executor-error-secret",
            ):
                assert raw not in persisted
        finally:
            await database.close()

    asyncio.run(scenario())


def test_failed_cancelled_and_illegal_terminal_overwrite_are_enforced(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _seed(database)
            recorder = SqliteToolInvocationRecorder(database)
            failed = _start("tool-invocation-failed-0001", 1)
            cancelled = _start("tool-invocation-cancel-0001", 2)
            await recorder.start(failed)
            await recorder.finish(
                ToolInvocationFinish.failed(
                    failed.invocation_id,
                    duration_ms=10,
                    finished_at=NOW + timedelta(seconds=3),
                )
            )
            await recorder.start(cancelled)
            await recorder.finish(
                ToolInvocationFinish.cancelled(
                    cancelled.invocation_id,
                    duration_ms=20,
                    finished_at=NOW + timedelta(seconds=4),
                )
            )

            async with database.create() as unit_of_work:
                rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT state, error_code FROM tool_invocations ORDER BY calls_used",
                )
                with pytest.raises(aiosqlite.IntegrityError, match="transition"):
                    await unit_of_work.connection.execute(
                        "UPDATE tool_invocations SET state = 'completed', error_code = NULL "
                        "WHERE id = ?",
                        (failed.invocation_id,),
                    )
                await unit_of_work.rollback()
            assert [(row["state"], row["error_code"]) for row in rows] == [
                ("failed", "tool_execution_failed"),
                ("cancelled", "tool_execution_cancelled"),
            ]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_event_failure_rolls_back_start_and_terminal_transition(tmp_path: Path) -> None:
    class FailingEventWriter(SqliteRunEventWriter):
        async def insert(
            self,
            connection: aiosqlite.Connection,
            event: RunEvent,
        ) -> RunEvent:
            del connection, event
            raise aiosqlite.OperationalError("forced Tool event failure")

    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _seed(database)
            failing = SqliteToolInvocationRecorder(
                database,
                FailingEventWriter(core_telemetry_redaction_service()),
            )
            start = _start("tool-invocation-rollback-01", 1)
            with pytest.raises(ToolInvocationPersistenceError, match="started"):
                await failing.start(start)
            async with database.create() as unit_of_work:
                assert await fetch_one(
                    unit_of_work.connection,
                    "SELECT id FROM tool_invocations WHERE id = ?",
                    (start.invocation_id,),
                ) is None
                await unit_of_work.commit()

            recorder = SqliteToolInvocationRecorder(database)
            await recorder.start(start)
            with pytest.raises(ToolInvocationPersistenceError, match="finalized"):
                await failing.finish(
                    ToolInvocationFinish.completed(
                        start.invocation_id,
                        duration_ms=25,
                        finished_at=NOW + timedelta(seconds=3),
                    )
                )
            async with database.create() as unit_of_work:
                row = await fetch_one(
                    unit_of_work.connection,
                    "SELECT state, duration_ms, finished_at FROM tool_invocations WHERE id = ?",
                    (start.invocation_id,),
                )
                await unit_of_work.commit()
            assert row is not None
            assert (row["state"], row["duration_ms"], row["finished_at"]) == (
                "running",
                0,
                None,
            )
        finally:
            await database.close()

    asyncio.run(scenario())
