"""SQLite persistence for Model Policy snapshots and governed Invocations."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.event_writer import SqliteRunEventWriter
from pangi.application.contracts.model_persistence import (
    ModelInvocationDenial,
    ModelInvocationFinish,
    ModelInvocationStart,
    ModelPolicySnapshot,
)
from pangi.application.contracts.model_routing import ModelEgressPolicy, ModelProfile
from pangi.application.contracts.run_events import RunEventDraft
from pangi.application.ports.model_persistence import (
    ModelInvocationPersistenceError,
    ModelPolicyPersistenceError,
)
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)
from pangi.domain.model_routing import ModelInvocationState
from pangi.domain.runs import EventVisibility


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class SqliteModelPolicyRepository:
    """Append draft snapshots and load the single active routing snapshot."""

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

    async def save_draft(
        self,
        snapshot: ModelPolicySnapshot,
        *,
        at: datetime,
    ) -> None:
        if not isinstance(snapshot, ModelPolicySnapshot):
            raise TypeError("snapshot must be a ModelPolicySnapshot")
        timestamp = _utc(at, field_name="at").isoformat()
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "INSERT INTO model_policies "
                    "(id, name, version, rules_json, fingerprint, state, eval_run_id, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'draft', NULL, ?, ?)",
                    (
                        snapshot.policy.policy_id,
                        snapshot.policy.profile,
                        snapshot.policy.policy_version,
                        snapshot.rules_json,
                        snapshot.fingerprint,
                        timestamp,
                        timestamp,
                    ),
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise ModelPolicyPersistenceError(
                "Model Policy draft could not be persisted"
            ) from error

    async def get_policy(self, profile: str) -> ModelEgressPolicy | None:
        snapshot = await self._active_snapshot(profile)
        return None if snapshot is None else snapshot.policy

    async def list_candidates(self, profile: str) -> tuple[ModelProfile, ...]:
        snapshot = await self._active_snapshot(profile)
        return () if snapshot is None else snapshot.profiles

    async def _active_snapshot(self, profile: str) -> ModelPolicySnapshot | None:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                row = await fetch_one(
                    unit_of_work.connection,
                    "SELECT id, name, version, rules_json, fingerprint "
                    "FROM model_policies WHERE name = ? AND state = 'active'",
                    (profile,),
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise ModelPolicyPersistenceError(
                "Active Model Policy could not be loaded"
            ) from error
        if row is None:
            return None
        try:
            snapshot = ModelPolicySnapshot.from_rules_json(str(row["rules_json"]))
            if (
                snapshot.policy.policy_id != str(row["id"])
                or snapshot.policy.profile != str(row["name"])
                or snapshot.policy.policy_version != str(row["version"])
                or snapshot.fingerprint != str(row["fingerprint"])
            ):
                raise ValueError("persisted Model Policy metadata does not match its rules")
            return snapshot
        except (KeyError, TypeError, ValueError) as error:
            raise ModelPolicyPersistenceError(
                "Persisted Model Policy is invalid"
            ) from error


class SqliteModelInvocationRecorder:
    """Persist each governed call and its internal Run Event atomically."""

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

    async def start(self, invocation: ModelInvocationStart) -> None:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._insert(
                    unit_of_work.connection,
                    invocation_id=invocation.invocation_id,
                    run_id=invocation.context.run_id,
                    step_id=invocation.context.step_id,
                    logical_call_fingerprint=invocation.logical_call_fingerprint,
                    decision=invocation.decision.as_dict(),
                    state=ModelInvocationState.RUNNING,
                    error_code=None,
                    created_at=invocation.started_at,
                    finished_at=None,
                )
                await self._append_event(
                    unit_of_work.connection,
                    RunEventDraft(
                        run_id=invocation.context.run_id,
                        step_id=invocation.context.step_id,
                        type="model.policy_allowed",
                        visibility=EventVisibility.INTERNAL,
                        created_at=invocation.started_at,
                        message="Model policy allowed the logical call",
                        attributes={"decision": invocation.decision.as_dict()},
                    ),
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise ModelInvocationPersistenceError(
                "Model Invocation could not be started"
            ) from error

    async def deny(self, invocation: ModelInvocationDenial) -> None:
        decision = invocation.decision.as_dict()
        error_code = invocation.decision.error_code
        assert error_code is not None
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._insert(
                    unit_of_work.connection,
                    invocation_id=invocation.invocation_id,
                    run_id=invocation.context.run_id,
                    step_id=invocation.context.step_id,
                    logical_call_fingerprint=invocation.logical_call_fingerprint,
                    decision=decision,
                    state=ModelInvocationState.DENIED,
                    error_code=error_code.value,
                    created_at=invocation.denied_at,
                    finished_at=invocation.denied_at,
                )
                await self._append_event(
                    unit_of_work.connection,
                    RunEventDraft(
                        run_id=invocation.context.run_id,
                        step_id=invocation.context.step_id,
                        type="model.policy_denied",
                        visibility=EventVisibility.INTERNAL,
                        created_at=invocation.denied_at,
                        message="Model policy denied the logical call",
                        attributes={"decision": decision},
                    ),
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise ModelInvocationPersistenceError(
                "Denied Model Invocation could not be persisted"
            ) from error

    async def finish(self, invocation: ModelInvocationFinish) -> None:
        token_usage = invocation.token_usage
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                cursor = await unit_of_work.connection.execute(
                    "UPDATE model_invocations SET state = ?, provider_requests = ?, "
                    "input_tokens = ?, output_tokens = ?, total_tokens = ?, duration_ms = ?, "
                    "provider_latency_ms = ?, finish_reason = ?, output_fingerprint = ?, "
                    "error_code = ?, finished_at = ? WHERE id = ? AND state = 'running'",
                    (
                        invocation.state.value,
                        invocation.provider_request_count,
                        None if token_usage is None else token_usage.input_tokens,
                        None if token_usage is None else token_usage.output_tokens,
                        None if token_usage is None else token_usage.total_tokens,
                        invocation.duration_ms,
                        invocation.provider_latency_ms,
                        (
                            None
                            if invocation.finish_reason is None
                            else invocation.finish_reason.value
                        ),
                        invocation.output_fingerprint,
                        invocation.error_code,
                        invocation.finished_at.isoformat(),
                        invocation.invocation_id,
                    ),
                )
                try:
                    if cursor.rowcount != 1:
                        raise ModelInvocationPersistenceError(
                            "Model Invocation is missing or already terminal"
                        )
                finally:
                    await cursor.close()
                await self._append_event(
                    unit_of_work.connection,
                    RunEventDraft(
                        run_id=await self._run_id(
                            unit_of_work.connection,
                            invocation.invocation_id,
                        ),
                        step_id=await self._step_id(
                            unit_of_work.connection,
                            invocation.invocation_id,
                        ),
                        type="model.invocation_completed",
                        visibility=EventVisibility.INTERNAL,
                        created_at=invocation.finished_at,
                        message="Model invocation reached a terminal state",
                        attributes=_finish_attributes(invocation),
                    ),
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise ModelInvocationPersistenceError(
                "Model Invocation could not be finalized"
            ) from error

    async def _insert(
        self,
        connection: aiosqlite.Connection,
        *,
        invocation_id: str,
        run_id: str,
        step_id: str | None,
        logical_call_fingerprint: str,
        decision: Mapping[str, object],
        state: ModelInvocationState,
        error_code: str | None,
        created_at: datetime,
        finished_at: datetime | None,
    ) -> None:
        redaction = decision.get("redaction")
        redaction_count = (
            redaction.get("redaction_count") if isinstance(redaction, dict) else None
        )
        await connection.execute(
            "INSERT INTO model_invocations "
            "(id, run_id, step_id, logical_call_fingerprint, role, provider, model, region, "
            "policy_id, policy_version, policy_fingerprint, profile_id, profile_fingerprint, "
            "data_classes_json, source_kinds_json, redaction_count, input_fingerprint, "
            "output_fingerprint, logical_calls, provider_requests, input_tokens, "
            "output_tokens, total_tokens, duration_ms, provider_latency_ms, finish_reason, "
            "state, error_code, created_at, finished_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, 0, NULL, "
            "NULL, NULL, 0, NULL, NULL, ?, ?, ?, ?)",
            (
                invocation_id,
                run_id,
                step_id,
                logical_call_fingerprint,
                decision["purpose"],
                decision.get("provider"),
                decision.get("model"),
                decision.get("region"),
                decision.get("policy_id"),
                decision.get("policy_version"),
                decision.get("policy_fingerprint"),
                decision.get("selected_profile_id"),
                decision.get("selected_profile_fingerprint"),
                _canonical_json(decision["data_classes"]),
                _canonical_json(decision["source_kinds"]),
                redaction_count,
                decision.get("input_fingerprint"),
                state.value,
                error_code,
                created_at.astimezone(UTC).isoformat(),
                None if finished_at is None else finished_at.astimezone(UTC).isoformat(),
            ),
        )

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
            raise ModelInvocationPersistenceError(
                "The next Model Run Event index is unavailable"
            )
        event = self._event_writer.prepare_draft(draft, index=int(row["value"]))
        await self._event_writer.insert(connection, event)

    @staticmethod
    async def _run_id(connection: aiosqlite.Connection, invocation_id: str) -> str:
        row = await fetch_one(
            connection,
            "SELECT run_id FROM model_invocations WHERE id = ?",
            (invocation_id,),
        )
        if row is None:
            raise ModelInvocationPersistenceError("Model Invocation Run is unavailable")
        return str(row["run_id"])

    @staticmethod
    async def _step_id(
        connection: aiosqlite.Connection,
        invocation_id: str,
    ) -> str | None:
        row = await fetch_one(
            connection,
            "SELECT step_id FROM model_invocations WHERE id = ?",
            (invocation_id,),
        )
        if row is None:
            raise ModelInvocationPersistenceError("Model Invocation Step is unavailable")
        return None if row["step_id"] is None else str(row["step_id"])


def _finish_attributes(invocation: ModelInvocationFinish) -> dict[str, object]:
    token_usage = invocation.token_usage
    return {
        "duration_ms": invocation.duration_ms,
        "error_code": invocation.error_code,
        "finish_reason": (
            None if invocation.finish_reason is None else invocation.finish_reason.value
        ),
        "logical_calls": 1,
        "output_fingerprint": invocation.output_fingerprint,
        "provider_latency_ms": invocation.provider_latency_ms,
        "provider_requests": invocation.provider_request_count,
        "state": invocation.state.value,
        "tokens": (
            None
            if token_usage is None
            else {
                "input": token_usage.input_tokens,
                "output": token_usage.output_tokens,
                "total": token_usage.total_tokens,
            }
        ),
    }


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
