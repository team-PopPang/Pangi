"""SQLite persistence for Model Policy snapshots and governed Invocations."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.audit import SqliteAuditWriter
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all, fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.event_writer import SqliteRunEventWriter
from pangi.application.contracts.audit import AuditEventDraft
from pangi.application.contracts.model_persistence import (
    ModelInvocationDenial,
    ModelInvocationFinish,
    ModelInvocationStart,
    ModelPolicySnapshot,
)
from pangi.application.contracts.model_policy_management import (
    ModelInvocationPurposeCount,
    ModelInvocationReasonCount,
    ModelInvocationSummary,
    ModelPolicyActivation,
    ModelPolicyActivationCommand,
    ModelPolicyListItem,
    ModelPolicyStoreQuery,
    ModelPolicyVersion,
    compare_model_policy_versions,
)
from pangi.application.contracts.model_routing import ModelEgressPolicy, ModelProfile
from pangi.application.contracts.run_events import RunEventDraft
from pangi.application.ports.model_persistence import (
    ModelInvocationPersistenceError,
)
from pangi.application.ports.model_persistence import (
    ModelPolicyPersistenceError as ModelRoutingPersistenceError,
)
from pangi.application.ports.model_policy_management import (
    ModelPolicyConflictError,
    ModelPolicyIdempotencyConflictError,
    ModelPolicyPersistenceError,
    ModelPolicyStaleImpactError,
)
from pangi.application.services.audit import core_audit_redaction_service
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)
from pangi.domain.audit import AuditOutcome
from pangi.domain.model_routing import ModelInvocationState, ModelPolicyState
from pangi.domain.runs import EventVisibility


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


_MODEL_POLICY_ACTIVATE_ROUTE = "model_policy.activate"


class SqliteModelPolicyRepository:
    """Append draft snapshots and load the single active routing snapshot."""

    def __init__(
        self,
        database: SqliteDatabase,
        audit_writer: SqliteAuditWriter | None = None,
    ) -> None:
        self._database = database
        self._audit_writer = audit_writer or SqliteAuditWriter(core_audit_redaction_service())

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
            raise ModelRoutingPersistenceError(
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
            raise ModelRoutingPersistenceError("Active Model Policy could not be loaded") from error
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
            raise ModelRoutingPersistenceError("Persisted Model Policy is invalid") from error

    async def list_management_items(
        self,
        query: ModelPolicyStoreQuery,
    ) -> tuple[ModelPolicyListItem, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        if query.after is not None:
            after_time = query.after.created_at.astimezone(UTC).isoformat()
            clauses.append(
                "(created_at < ? OR (created_at = ? AND (id > ? OR (id = ? AND version > ?))))"
            )
            parameters.extend(
                (
                    after_time,
                    after_time,
                    query.after.policy_id,
                    query.after.policy_id,
                    query.after.version,
                )
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(query.limit)
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT id, name, version, rules_json, fingerprint, state, "
                    "eval_run_id, created_at, updated_at FROM model_policies"
                    + where
                    + " ORDER BY created_at DESC, id, version LIMIT ?",
                    tuple(parameters),
                )
                versions = tuple(_policy_version(row) for row in rows)
                active_by_profile: dict[str, ModelPolicyVersion | None] = {}
                items: list[ModelPolicyListItem] = []
                for version in versions:
                    baseline = active_by_profile.get(version.profile)
                    if version.profile not in active_by_profile:
                        baseline_row = await fetch_one(
                            unit_of_work.connection,
                            "SELECT id, name, version, rules_json, fingerprint, state, "
                            "eval_run_id, created_at, updated_at FROM model_policies "
                            "WHERE name = ? AND state = 'active'",
                            (version.profile,),
                        )
                        baseline = None if baseline_row is None else _policy_version(baseline_row)
                        active_by_profile[version.profile] = baseline
                    summary = await _invocation_summary(
                        unit_of_work.connection,
                        profile=version.profile,
                        started_at=query.summary_started_at,
                        ended_at=query.summary_ended_at,
                    )
                    impact = (
                        compare_model_policy_versions(baseline, version)
                        if version.state is ModelPolicyState.DRAFT
                        else None
                    )
                    items.append(ModelPolicyListItem(version, summary, impact))
                await unit_of_work.commit()
                return tuple(items)
        except (KeyError, TypeError, ValueError) as error:
            raise ModelPolicyPersistenceError(
                "Persisted Model Policy management data is invalid"
            ) from error
        except aiosqlite.Error as error:
            raise ModelPolicyPersistenceError(
                "Model Policy management data could not be read"
            ) from error

    async def get_version(
        self,
        policy_id: str,
        version: str,
    ) -> ModelPolicyVersion | None:
        return await self._management_version(
            "WHERE id = ? AND version = ?",
            (policy_id, version),
        )

    async def get_active_version(self, profile: str) -> ModelPolicyVersion | None:
        return await self._management_version(
            "WHERE name = ? AND state = 'active'",
            (profile,),
        )

    async def _management_version(
        self,
        where: str,
        parameters: tuple[object, ...],
    ) -> ModelPolicyVersion | None:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                row = await fetch_one(
                    unit_of_work.connection,
                    "SELECT id, name, version, rules_json, fingerprint, state, "
                    "eval_run_id, created_at, updated_at FROM model_policies " + where,
                    parameters,
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise ModelPolicyPersistenceError(
                "Model Policy management data could not be read"
            ) from error
        if row is None:
            return None
        try:
            return _policy_version(row)
        except (KeyError, TypeError, ValueError) as error:
            raise ModelPolicyPersistenceError(
                "Persisted Model Policy management data is invalid"
            ) from error

    async def get_activation_replay(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        recorded_at: datetime,
    ) -> ModelPolicyActivation | None:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                connection = unit_of_work.connection
                await connection.execute(
                    "DELETE FROM api_idempotency_records WHERE principal_id = ? "
                    "AND route_key = ? AND idempotency_key = ? AND expires_at <= ?",
                    (
                        actor_id,
                        _MODEL_POLICY_ACTIVATE_ROUTE,
                        idempotency_key,
                        recorded_at.astimezone(UTC).isoformat(),
                    ),
                )
                replay = await fetch_one(
                    connection,
                    "SELECT request_fingerprint, response_json, state "
                    "FROM api_idempotency_records WHERE principal_id = ? "
                    "AND route_key = ? AND idempotency_key = ?",
                    (actor_id, _MODEL_POLICY_ACTIVATE_ROUTE, idempotency_key),
                )
                if replay is None:
                    await unit_of_work.commit()
                    return None
                if str(replay["request_fingerprint"]) != request_fingerprint:
                    raise ModelPolicyIdempotencyConflictError(
                        "The Idempotency-Key belongs to another request"
                    )
                if str(replay["state"]) != "completed":
                    raise ModelPolicyConflictError(
                        "The Model Policy activation is already processing"
                    )
                result = await _activation_replay(connection, replay)
                await unit_of_work.commit()
                return result
        except (
            ModelPolicyConflictError,
            ModelPolicyIdempotencyConflictError,
        ):
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ModelPolicyPersistenceError(
                "Persisted Model Policy activation replay is invalid"
            ) from error
        except aiosqlite.Error as error:
            raise ModelPolicyPersistenceError(
                "Model Policy activation replay could not be read"
            ) from error

    async def activate(
        self,
        command: ModelPolicyActivationCommand,
    ) -> ModelPolicyActivation:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                connection = unit_of_work.connection
                await connection.execute(
                    "DELETE FROM api_idempotency_records WHERE principal_id = ? "
                    "AND route_key = ? AND idempotency_key = ? AND expires_at <= ?",
                    (
                        command.actor_id,
                        _MODEL_POLICY_ACTIVATE_ROUTE,
                        command.idempotency_key,
                        command.recorded_at.isoformat(),
                    ),
                )
                replay = await fetch_one(
                    connection,
                    "SELECT request_fingerprint, response_json, state "
                    "FROM api_idempotency_records WHERE principal_id = ? "
                    "AND route_key = ? AND idempotency_key = ?",
                    (
                        command.actor_id,
                        _MODEL_POLICY_ACTIVATE_ROUTE,
                        command.idempotency_key,
                    ),
                )
                if replay is not None:
                    if str(replay["request_fingerprint"]) != command.request_fingerprint:
                        raise ModelPolicyIdempotencyConflictError(
                            "The Idempotency-Key belongs to another request"
                        )
                    if str(replay["state"]) != "completed":
                        raise ModelPolicyConflictError(
                            "The Model Policy activation is already processing"
                        )
                    result = await _activation_replay(connection, replay)
                    await unit_of_work.commit()
                    return result

                await connection.execute(
                    "INSERT INTO api_idempotency_records "
                    "(principal_id, route_key, idempotency_key, request_fingerprint, "
                    "response_json, state, run_id, expires_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, NULL, 'processing', NULL, ?, ?, ?)",
                    (
                        command.actor_id,
                        _MODEL_POLICY_ACTIVATE_ROUTE,
                        command.idempotency_key,
                        command.request_fingerprint,
                        command.expires_at.isoformat(),
                        command.recorded_at.isoformat(),
                        command.recorded_at.isoformat(),
                    ),
                )
                candidate_row = await fetch_one(
                    connection,
                    "SELECT id, name, version, rules_json, fingerprint, state, "
                    "eval_run_id, created_at, updated_at FROM model_policies "
                    "WHERE id = ? AND version = ?",
                    (command.policy_id, command.version),
                )
                if candidate_row is None:
                    raise ModelPolicyConflictError("The candidate Policy is unavailable")
                candidate = _policy_version(candidate_row)
                if candidate.state is not ModelPolicyState.DRAFT:
                    raise ModelPolicyConflictError("Only a draft Policy can be activated")
                if candidate.fingerprint != command.candidate_fingerprint:
                    raise ModelPolicyStaleImpactError("The candidate Policy fingerprint changed")
                baseline_row = await fetch_one(
                    connection,
                    "SELECT id, name, version, rules_json, fingerprint, state, "
                    "eval_run_id, created_at, updated_at FROM model_policies "
                    "WHERE name = ? AND state = 'active'",
                    (candidate.profile,),
                )
                baseline = None if baseline_row is None else _policy_version(baseline_row)
                baseline_fingerprint = None if baseline is None else baseline.fingerprint
                if baseline_fingerprint != command.expected_baseline_fingerprint:
                    raise ModelPolicyStaleImpactError("The active Policy changed")
                impact = compare_model_policy_versions(baseline, candidate)
                if impact.fingerprint != command.impact_fingerprint:
                    raise ModelPolicyStaleImpactError("The Model Policy impact changed")

                if baseline is not None:
                    await connection.execute(
                        "UPDATE model_policies SET state = 'retired', updated_at = ? "
                        "WHERE id = ? AND version = ? AND state = 'active'",
                        (
                            command.recorded_at.isoformat(),
                            baseline.policy_id,
                            baseline.version,
                        ),
                    )
                cursor = await connection.execute(
                    "UPDATE model_policies SET state = 'active', eval_run_id = ?, "
                    "updated_at = ? WHERE id = ? AND version = ? AND state = 'draft'",
                    (
                        command.eval_run_id,
                        command.recorded_at.isoformat(),
                        command.policy_id,
                        command.version,
                    ),
                )
                try:
                    updated = cursor.rowcount
                finally:
                    await cursor.close()
                if updated != 1:
                    raise ModelPolicyConflictError("The candidate Policy state changed")
                await self._audit_writer.insert(
                    connection,
                    AuditEventDraft(
                        actor_id=command.actor_id,
                        action="model_policy.version_activated",
                        resource_type="model_policy",
                        resource_id=f"{command.policy_id}:{command.version}",
                        outcome=AuditOutcome.SUCCEEDED,
                        created_at=command.recorded_at,
                        before_summary=(
                            None
                            if baseline is None
                            else {
                                "fingerprint": baseline.fingerprint,
                                "policy_id": baseline.policy_id,
                                "state": baseline.state.value,
                                "version": baseline.version,
                            }
                        ),
                        after_summary={
                            "fingerprint": candidate.fingerprint,
                            "policy_id": candidate.policy_id,
                            "state": ModelPolicyState.ACTIVE.value,
                            "version": candidate.version,
                        },
                        details={
                            "eval_run_id": command.eval_run_id,
                            "impact_fingerprint": command.impact_fingerprint,
                        },
                    ),
                )
                response = _canonical_json(
                    {
                        "activated_at": command.recorded_at.isoformat(),
                        "eval_run_id": command.eval_run_id,
                        "impact_fingerprint": command.impact_fingerprint,
                        "policy_id": command.policy_id,
                        "version": command.version,
                    }
                )
                await connection.execute(
                    "UPDATE api_idempotency_records SET response_json = ?, "
                    "state = 'completed', updated_at = ? WHERE principal_id = ? "
                    "AND route_key = ? AND idempotency_key = ? AND state = 'processing'",
                    (
                        response,
                        command.recorded_at.isoformat(),
                        command.actor_id,
                        _MODEL_POLICY_ACTIVATE_ROUTE,
                        command.idempotency_key,
                    ),
                )
                await unit_of_work.commit()
                activated = ModelPolicyVersion(
                    candidate.snapshot,
                    ModelPolicyState.ACTIVE,
                    command.eval_run_id,
                    candidate.created_at,
                    command.recorded_at,
                )
                return ModelPolicyActivation(
                    activated,
                    command.impact_fingerprint,
                    False,
                )
        except (
            ModelPolicyConflictError,
            ModelPolicyIdempotencyConflictError,
            ModelPolicyStaleImpactError,
        ):
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ModelPolicyPersistenceError(
                "Persisted Model Policy activation data is invalid"
            ) from error
        except aiosqlite.Error as error:
            raise ModelPolicyPersistenceError(
                "Model Policy activation could not be persisted"
            ) from error


async def _activation_replay(
    connection: aiosqlite.Connection,
    replay: aiosqlite.Row,
) -> ModelPolicyActivation:
    response = json.loads(str(replay["response_json"]))
    if not isinstance(response, dict):
        raise ValueError("activation replay response must be an object")
    replay_row = await fetch_one(
        connection,
        "SELECT id, name, version, rules_json, fingerprint, state, "
        "eval_run_id, created_at, updated_at FROM model_policies "
        "WHERE id = ? AND version = ?",
        (response.get("policy_id"), response.get("version")),
    )
    if replay_row is None:
        raise ValueError("activation replay Policy is unavailable")
    replay_policy = _policy_version(replay_row)
    return ModelPolicyActivation(
        ModelPolicyVersion(
            replay_policy.snapshot,
            ModelPolicyState.ACTIVE,
            str(response.get("eval_run_id")),
            replay_policy.created_at,
            datetime.fromisoformat(str(response.get("activated_at"))),
        ),
        str(response.get("impact_fingerprint")),
        True,
    )


def _policy_version(row: aiosqlite.Row) -> ModelPolicyVersion:
    snapshot = ModelPolicySnapshot.from_rules_json(str(row["rules_json"]))
    if (
        snapshot.policy.policy_id != str(row["id"])
        or snapshot.policy.profile != str(row["name"])
        or snapshot.policy.policy_version != str(row["version"])
        or snapshot.fingerprint != str(row["fingerprint"])
    ):
        raise ValueError("persisted Model Policy metadata does not match its rules")
    return ModelPolicyVersion(
        snapshot=snapshot,
        state=ModelPolicyState(str(row["state"])),
        eval_run_id=(None if row["eval_run_id"] is None else str(row["eval_run_id"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


async def _invocation_summary(
    connection: aiosqlite.Connection,
    *,
    profile: str,
    started_at: datetime,
    ended_at: datetime,
) -> ModelInvocationSummary:
    parameters = (
        profile,
        started_at.astimezone(UTC).isoformat(),
        ended_at.astimezone(UTC).isoformat(),
    )
    totals = await fetch_one(
        connection,
        "SELECT COALESCE(SUM(CASE WHEN state = 'denied' THEN 0 ELSE 1 END), 0) "
        "AS allowed_count, COALESCE(SUM(CASE WHEN state = 'denied' THEN 1 ELSE 0 END), 0) "
        "AS denied_count FROM model_invocations WHERE requested_profile = ? "
        "AND created_at >= ? AND created_at < ?",
        parameters,
    )
    if totals is None:
        raise ValueError("Model Invocation summary totals are unavailable")
    purpose_rows = await fetch_all(
        connection,
        "SELECT role, COUNT(*) AS count FROM model_invocations "
        "WHERE requested_profile = ? AND created_at >= ? AND created_at < ? "
        "GROUP BY role ORDER BY role",
        parameters,
    )
    reason_rows = await fetch_all(
        connection,
        "SELECT error_code, COUNT(*) AS count FROM model_invocations "
        "WHERE requested_profile = ? AND created_at >= ? AND created_at < ? "
        "AND state = 'denied' GROUP BY error_code ORDER BY error_code",
        parameters,
    )
    return ModelInvocationSummary(
        window_started_at=started_at,
        window_ended_at=ended_at,
        allowed_count=int(totals["allowed_count"]),
        denied_count=int(totals["denied_count"]),
        purposes=tuple(
            ModelInvocationPurposeCount(str(row["role"]), int(row["count"])) for row in purpose_rows
        ),
        denial_reasons=tuple(
            ModelInvocationReasonCount(str(row["error_code"]), int(row["count"]))
            for row in reason_rows
        ),
    )


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
        redaction_count = redaction.get("redaction_count") if isinstance(redaction, dict) else None
        await connection.execute(
            "INSERT INTO model_invocations "
            "(id, run_id, step_id, logical_call_fingerprint, requested_profile, role, "
            "provider, model, region, "
            "policy_id, policy_version, policy_fingerprint, profile_id, profile_fingerprint, "
            "data_classes_json, source_kinds_json, redaction_count, input_fingerprint, "
            "output_fingerprint, logical_calls, provider_requests, input_tokens, "
            "output_tokens, total_tokens, duration_ms, provider_latency_ms, finish_reason, "
            "state, error_code, created_at, finished_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, 0, NULL, "
            "NULL, NULL, 0, NULL, NULL, ?, ?, ?, ?)",
            (
                invocation_id,
                run_id,
                step_id,
                logical_call_fingerprint,
                decision["profile"],
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
            "SELECT COALESCE(MAX(event_index), 0) + 1 AS value FROM run_events WHERE run_id = ?",
            (draft.run_id,),
        )
        if row is None:
            raise ModelInvocationPersistenceError("The next Model Run Event index is unavailable")
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
