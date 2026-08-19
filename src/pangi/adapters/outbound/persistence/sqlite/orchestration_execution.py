"""SQLite persistence for immutable Plans and owned Run Step execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all, fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.event_writer import SqliteRunEventWriter
from pangi.application.contracts.orchestration import AgentResult
from pangi.application.contracts.orchestration_execution import (
    EXECUTION_PLAN_SCHEMA_VERSION,
    ExecutionPlanSnapshot,
    ExecutionStepSnapshot,
    PreparedExecutionPlan,
    PreparedExecutionStep,
    agent_result_data,
    agent_result_from_data,
    canonical_execution_json,
    execution_plan_data,
    prepared_execution_plan_from_data,
    prepared_execution_step_data,
    prepared_execution_step_from_data,
)
from pangi.application.contracts.run_queue import RunClaim
from pangi.application.ports.orchestration_execution import (
    ExecutionOwnershipLostError,
    ExecutionPersistenceError,
    ExecutionPlanConflictError,
    ExecutionPlanNotFoundError,
    OrchestrationExecutionError,
)
from pangi.application.services.redaction import RedactionService
from pangi.domain.runs import (
    EventVisibility,
    RunErrorCode,
    RunEvent,
    RunState,
    RunStep,
    StepRequirement,
    StepState,
)


class SqliteOrchestrationExecutionStore:
    def __init__(
        self,
        database: SqliteDatabase,
        redactor: RedactionService,
        event_writer: SqliteRunEventWriter,
    ) -> None:
        self._database = database
        self._redactor = redactor
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

    async def materialize_and_enqueue(
        self,
        *,
        run_id: str,
        expected_revision: int,
        plan: PreparedExecutionPlan,
        at: datetime,
    ) -> ExecutionPlanSnapshot:
        timestamp = _utc(at)
        raw_fingerprint = plan.fingerprint
        safe_plan = self._safe_plan(plan)
        safe_plan_json = canonical_execution_json(execution_plan_data(safe_plan))
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                existing = await fetch_one(
                    unit_of_work.connection,
                    "SELECT plan_fingerprint FROM run_execution_plans WHERE run_id = ?",
                    (run_id,),
                )
                if existing is not None:
                    if str(existing["plan_fingerprint"]) != raw_fingerprint:
                        raise ExecutionPlanConflictError(
                            "A different execution Plan already exists"
                        )
                    snapshot = await self._snapshot(unit_of_work.connection, run_id)
                    await unit_of_work.commit()
                    return snapshot

                row = await fetch_one(
                    unit_of_work.connection,
                    "SELECT state, revision FROM runs WHERE id = ?",
                    (run_id,),
                )
                if row is None:
                    raise ExecutionPlanNotFoundError("The execution Run was not found")
                if int(row["revision"]) != expected_revision or str(row["state"]) not in {
                    RunState.RECEIVED.value,
                    RunState.PLANNING.value,
                }:
                    raise ExecutionPlanConflictError("The Run cannot accept an execution Plan")

                await unit_of_work.connection.execute(
                    "INSERT INTO run_execution_plans "
                    "(run_id, mode, schema_version, plan_json, plan_fingerprint, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        safe_plan.mode.value,
                        EXECUTION_PLAN_SCHEMA_VERSION,
                        safe_plan_json,
                        raw_fingerprint,
                        timestamp.isoformat(),
                    ),
                )
                for step in safe_plan.steps:
                    await self._insert_step(
                        unit_of_work.connection,
                        run_id=run_id,
                        definition=step,
                        attempt=1,
                        at=timestamp,
                    )
                cursor = await unit_of_work.connection.execute(
                    "UPDATE runs SET state = 'queued', mode = ?, revision = revision + 1, "
                    "worker_id = NULL, lease_expires_at = NULL, heartbeat_at = NULL, "
                    "error_code = NULL, updated_at = ?, queued_at = ? "
                    "WHERE id = ? AND revision = ? AND state IN ('received', 'planning')",
                    (
                        safe_plan.mode.value,
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                        run_id,
                        expected_revision,
                    ),
                )
                try:
                    if cursor.rowcount != 1:
                        raise ExecutionPlanConflictError(
                            "The Run revision changed while storing its Plan"
                        )
                finally:
                    await cursor.close()
                await self._append_event(
                    unit_of_work.connection,
                    run_id=run_id,
                    event_type="run.queued",
                    visibility=EventVisibility.PUBLIC,
                    at=timestamp,
                    message="Run queued",
                    attributes={"reason": "orchestration_plan_ready"},
                )
                step_rows = await fetch_all(
                    unit_of_work.connection,
                    "SELECT id, node_id, attempt FROM run_steps WHERE run_id = ? ORDER BY rowid",
                    (run_id,),
                )
                for step_row in step_rows:
                    await self._append_event(
                        unit_of_work.connection,
                        run_id=run_id,
                        step_id=str(step_row["id"]),
                        event_type="step.queued",
                        visibility=EventVisibility.INTERNAL,
                        at=timestamp,
                        message="Run Step queued",
                        attributes={
                            "attempt": int(step_row["attempt"]),
                            "node_id": str(step_row["node_id"]),
                        },
                    )
                snapshot = await self._snapshot(unit_of_work.connection, run_id)
                await unit_of_work.commit()
                return snapshot
        except OrchestrationExecutionError:
            raise
        except (aiosqlite.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ExecutionPersistenceError("Execution Plan persistence failed") from error

    async def load_for_claim(
        self,
        claim: RunClaim,
        *,
        at: datetime,
    ) -> ExecutionPlanSnapshot:
        timestamp = _utc(at)
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._require_owner(
                    unit_of_work.connection,
                    claim=claim,
                    at=timestamp,
                )
                snapshot = await self._snapshot(unit_of_work.connection, claim.run_id)
                await unit_of_work.commit()
                return snapshot
        except OrchestrationExecutionError:
            raise
        except (aiosqlite.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ExecutionPersistenceError("Execution Plan loading failed") from error

    async def retry_interrupted_step(
        self,
        *,
        claim: RunClaim,
        step: ExecutionStepSnapshot,
        at: datetime,
    ) -> ExecutionStepSnapshot:
        timestamp = _utc(at)
        if step.step.state is not StepState.INTERRUPTED or not step.step.idempotent:
            raise ExecutionPlanConflictError("Only interrupted idempotent Steps can retry")
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._require_owner(unit_of_work.connection, claim=claim, at=timestamp)
                latest = await fetch_one(
                    unit_of_work.connection,
                    "SELECT attempt, state FROM run_steps "
                    "WHERE run_id = ? AND node_id = ? ORDER BY attempt DESC LIMIT 1",
                    (claim.run_id, step.step.node_id),
                )
                if (
                    latest is None
                    or int(latest["attempt"]) != step.step.attempt
                    or str(latest["state"]) != StepState.INTERRUPTED.value
                ):
                    raise ExecutionPlanConflictError("The interrupted Step changed")
                retried = await self._insert_step(
                    unit_of_work.connection,
                    run_id=claim.run_id,
                    definition=step.definition,
                    attempt=step.step.attempt + 1,
                    at=timestamp,
                )
                await self._append_event(
                    unit_of_work.connection,
                    run_id=claim.run_id,
                    step_id=retried.step.id,
                    event_type="step.queued",
                    visibility=EventVisibility.INTERNAL,
                    at=timestamp,
                    message="Run Step queued for retry",
                    attributes={
                        "attempt": retried.step.attempt,
                        "node_id": retried.step.node_id,
                    },
                )
                await unit_of_work.commit()
                return retried
        except OrchestrationExecutionError:
            raise
        except (aiosqlite.Error, KeyError, TypeError, ValueError) as error:
            raise ExecutionPersistenceError("Execution Step retry failed") from error

    async def start_step(
        self,
        *,
        claim: RunClaim,
        step: ExecutionStepSnapshot,
        at: datetime,
    ) -> ExecutionStepSnapshot:
        timestamp = _utc(at)
        if step.step.state is not StepState.QUEUED:
            raise ExecutionPlanConflictError("Only queued Steps can start")
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._require_owner(unit_of_work.connection, claim=claim, at=timestamp)
                cursor = await unit_of_work.connection.execute(
                    "UPDATE run_steps SET state = 'running', started_at = ?, updated_at = ? "
                    "WHERE id = ? AND run_id = ? AND state = 'queued' AND attempt = ?",
                    (
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                        step.step.id,
                        claim.run_id,
                        step.step.attempt,
                    ),
                )
                try:
                    if cursor.rowcount != 1:
                        raise ExecutionPlanConflictError("The queued Step changed")
                finally:
                    await cursor.close()
                await self._append_event(
                    unit_of_work.connection,
                    run_id=claim.run_id,
                    step_id=step.step.id,
                    event_type="step.running",
                    visibility=EventVisibility.PUBLIC,
                    at=timestamp,
                    message="Run Step started",
                    attributes={"attempt": step.step.attempt},
                )
                changed = await self._step_by_id(unit_of_work.connection, step.step.id)
                await unit_of_work.commit()
                return changed
        except OrchestrationExecutionError:
            raise
        except (aiosqlite.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ExecutionPersistenceError("Execution Step start failed") from error

    async def finish_step(
        self,
        *,
        claim: RunClaim,
        step: ExecutionStepSnapshot,
        state: StepState,
        result: AgentResult,
        error_code: str | None,
        at: datetime,
    ) -> ExecutionStepSnapshot:
        timestamp = _utc(at)
        if state not in {StepState.COMPLETED, StepState.FAILED}:
            raise ValueError("Execution Step finish state must be completed or failed")
        if result.task_id != step.step.node_id:
            raise ValueError("AgentResult belongs to another Step")
        raw_data = agent_result_data(result)
        result_fingerprint = _fingerprint(raw_data)
        safe_result = self._safe_result(result)
        result_json = canonical_execution_json(agent_result_data(safe_result))
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._require_owner(unit_of_work.connection, claim=claim, at=timestamp)
                cursor = await unit_of_work.connection.execute(
                    "UPDATE run_steps SET state = ?, result_json = ?, result_fingerprint = ?, "
                    "error_code = ?, updated_at = ?, finished_at = ? "
                    "WHERE id = ? AND run_id = ? AND state = 'running' AND attempt = ?",
                    (
                        state.value,
                        result_json,
                        result_fingerprint,
                        error_code,
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                        step.step.id,
                        claim.run_id,
                        step.step.attempt,
                    ),
                )
                try:
                    if cursor.rowcount != 1:
                        raise ExecutionPlanConflictError("The running Step changed")
                finally:
                    await cursor.close()
                await self._append_event(
                    unit_of_work.connection,
                    run_id=claim.run_id,
                    step_id=step.step.id,
                    event_type=f"step.{state.value}",
                    visibility=EventVisibility.PUBLIC,
                    at=timestamp,
                    message=f"Run Step {state.value}",
                    attributes={
                        "attempt": step.step.attempt,
                        "error_code": error_code,
                        "result_status": safe_result.status.value,
                    },
                )
                changed = await self._step_by_id(unit_of_work.connection, step.step.id)
                await unit_of_work.commit()
                return changed
        except OrchestrationExecutionError:
            raise
        except (aiosqlite.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ExecutionPersistenceError("Execution Step completion failed") from error

    async def cancel_step(
        self,
        *,
        claim: RunClaim,
        step: ExecutionStepSnapshot,
        error_code: str,
        at: datetime,
    ) -> ExecutionStepSnapshot:
        timestamp = _utc(at)
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._require_owner(unit_of_work.connection, claim=claim, at=timestamp)
                cursor = await unit_of_work.connection.execute(
                    "UPDATE run_steps SET state = 'cancelled', error_code = ?, "
                    "updated_at = ?, finished_at = ? "
                    "WHERE id = ? AND run_id = ? AND state = 'queued' AND attempt = ?",
                    (
                        error_code,
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                        step.step.id,
                        claim.run_id,
                        step.step.attempt,
                    ),
                )
                try:
                    if cursor.rowcount != 1:
                        raise ExecutionPlanConflictError("The queued Step changed")
                finally:
                    await cursor.close()
                await self._append_event(
                    unit_of_work.connection,
                    run_id=claim.run_id,
                    step_id=step.step.id,
                    event_type="step.cancelled",
                    visibility=EventVisibility.PUBLIC,
                    at=timestamp,
                    message="Run Step cancelled",
                    attributes={"attempt": step.step.attempt, "error_code": error_code},
                )
                changed = await self._step_by_id(unit_of_work.connection, step.step.id)
                await unit_of_work.commit()
                return changed
        except OrchestrationExecutionError:
            raise
        except (aiosqlite.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ExecutionPersistenceError("Execution Step cancellation failed") from error

    async def finish_run(
        self,
        *,
        claim: RunClaim,
        state: RunState,
        warnings: tuple[str, ...],
        error_code: RunErrorCode | None,
        at: datetime,
    ) -> None:
        timestamp = _utc(at)
        if state not in {RunState.COMPOSING, RunState.FAILED}:
            raise ValueError("Execution Run finish state must be composing or failed")
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await self._require_owner(unit_of_work.connection, claim=claim, at=timestamp)
                cursor = await unit_of_work.connection.execute(
                    "UPDATE runs SET state = ?, revision = revision + 1, worker_id = NULL, "
                    "lease_expires_at = NULL, heartbeat_at = NULL, warnings_json = ?, "
                    "error_code = ?, updated_at = ?, "
                    "finished_at = CASE WHEN ? = 'failed' THEN ? ELSE NULL END "
                    "WHERE id = ? AND state = 'running' AND worker_id = ? "
                    "AND julianday(lease_expires_at) > julianday(?)",
                    (
                        state.value,
                        canonical_execution_json(list(warnings)),
                        error_code.value if error_code is not None else None,
                        timestamp.isoformat(),
                        state.value,
                        timestamp.isoformat(),
                        claim.run_id,
                        claim.worker_id,
                        timestamp.isoformat(),
                    ),
                )
                try:
                    if cursor.rowcount != 1:
                        raise ExecutionOwnershipLostError("Execution ownership was lost")
                finally:
                    await cursor.close()
                await self._append_event(
                    unit_of_work.connection,
                    run_id=claim.run_id,
                    event_type=f"run.{state.value}",
                    visibility=EventVisibility.PUBLIC,
                    at=timestamp,
                    message=f"Run {state.value}",
                    attributes={
                        "error_code": error_code.value if error_code is not None else None,
                        "warning_count": len(warnings),
                    },
                )
                await unit_of_work.commit()
        except OrchestrationExecutionError:
            raise
        except (aiosqlite.Error, KeyError, TypeError, ValueError) as error:
            raise ExecutionPersistenceError("Execution Run completion failed") from error

    async def _insert_step(
        self,
        connection: aiosqlite.Connection,
        *,
        run_id: str,
        definition: PreparedExecutionStep,
        attempt: int,
        at: datetime,
    ) -> ExecutionStepSnapshot:
        step_id = _step_id(run_id, definition.task.id, attempt)
        task_json = canonical_execution_json(prepared_execution_step_data(definition))
        await connection.execute(
            "INSERT INTO run_steps "
            "(id, run_id, node_id, type, state, requirement, idempotent, attempt, "
            "depends_on_json, task_json, created_at, updated_at) "
            "VALUES (?, ?, ?, 'subagent', 'queued', ?, ?, ?, ?, ?, ?, ?)",
            (
                step_id,
                run_id,
                definition.task.id,
                definition.requirement.value,
                int(definition.idempotent),
                attempt,
                canonical_execution_json(list(definition.task.depends_on)),
                task_json,
                at.isoformat(),
                at.isoformat(),
            ),
        )
        return await self._step_by_id(connection, step_id)

    async def _snapshot(
        self,
        connection: aiosqlite.Connection,
        run_id: str,
    ) -> ExecutionPlanSnapshot:
        plan_row = await fetch_one(
            connection,
            "SELECT plan_json, plan_fingerprint FROM run_execution_plans WHERE run_id = ?",
            (run_id,),
        )
        if plan_row is None:
            raise ExecutionPlanNotFoundError("The execution Plan was not found")
        plan = prepared_execution_plan_from_data(json.loads(str(plan_row["plan_json"])))
        rows = await fetch_all(
            connection,
            "SELECT id, run_id, node_id, type, state, requirement, idempotent, attempt, "
            "depends_on_json, task_json, result_json, error_code, created_at, updated_at, "
            "started_at, finished_at FROM run_steps WHERE run_id = ? "
            "ORDER BY node_id, attempt DESC",
            (run_id,),
        )
        latest: dict[str, aiosqlite.Row] = {}
        for row in rows:
            latest.setdefault(str(row["node_id"]), row)
        expected = tuple(step.task.id for step in plan.steps)
        if set(latest) != set(expected):
            raise ExecutionPersistenceError("Persisted execution Steps do not match the Plan")
        steps = tuple(self._step_from_row(latest[node_id]) for node_id in expected)
        return ExecutionPlanSnapshot(
            plan=plan,
            plan_fingerprint=str(plan_row["plan_fingerprint"]),
            steps=steps,
        )

    async def _step_by_id(
        self,
        connection: aiosqlite.Connection,
        step_id: str,
    ) -> ExecutionStepSnapshot:
        row = await fetch_one(
            connection,
            "SELECT id, run_id, node_id, type, state, requirement, idempotent, attempt, "
            "depends_on_json, task_json, result_json, error_code, created_at, updated_at, "
            "started_at, finished_at FROM run_steps WHERE id = ?",
            (step_id,),
        )
        if row is None:
            raise ExecutionPlanNotFoundError("The execution Step was not found")
        return self._step_from_row(row)

    def _step_from_row(self, row: aiosqlite.Row) -> ExecutionStepSnapshot:
        task_json = row["task_json"]
        if task_json is None:
            raise ExecutionPersistenceError("Persisted execution Step has no definition")
        definition = prepared_execution_step_from_data(json.loads(str(task_json)))
        result_json = row["result_json"]
        result = (
            None if result_json is None else agent_result_from_data(json.loads(str(result_json)))
        )
        depends_on = json.loads(str(row["depends_on_json"]))
        if not isinstance(depends_on, list) or any(
            not isinstance(item, str) for item in depends_on
        ):
            raise ExecutionPersistenceError("Persisted Step dependencies are invalid")
        step = RunStep(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            node_id=str(row["node_id"]),
            type=str(row["type"]),
            state=StepState(str(row["state"])),
            requirement=StepRequirement(str(row["requirement"])),
            idempotent=bool(int(row["idempotent"])),
            attempt=int(row["attempt"]),
            depends_on=tuple(depends_on),
            error_code=None if row["error_code"] is None else str(row["error_code"]),
            created_at=_datetime(row, "created_at"),
            updated_at=_datetime(row, "updated_at"),
            started_at=_optional_datetime(row, "started_at"),
            finished_at=_optional_datetime(row, "finished_at"),
        )
        return ExecutionStepSnapshot(step, definition, result)

    async def _require_owner(
        self,
        connection: aiosqlite.Connection,
        *,
        claim: RunClaim,
        at: datetime,
    ) -> None:
        row = await fetch_one(
            connection,
            "SELECT 1 FROM runs WHERE id = ? AND state = 'running' AND worker_id = ? "
            "AND julianday(lease_expires_at) > julianday(?)",
            (claim.run_id, claim.worker_id, at.isoformat()),
        )
        if row is None:
            raise ExecutionOwnershipLostError("Execution ownership was lost")

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
        step_id: str | None = None,
    ) -> None:
        row = await fetch_one(
            connection,
            "SELECT COALESCE(MAX(event_index), 0) + 1 AS next_index "
            "FROM run_events WHERE run_id = ?",
            (run_id,),
        )
        if row is None:
            raise ExecutionPersistenceError("The next Run Event index is unavailable")
        await self._event_writer.insert(
            connection,
            RunEvent(
                run_id=run_id,
                index=int(row["next_index"]),
                type=event_type,
                visibility=visibility,
                created_at=at,
                step_id=step_id,
                message=message,
                attributes=attributes,
            ),
        )

    def _safe_plan(self, plan: PreparedExecutionPlan) -> PreparedExecutionPlan:
        value = self._redactor.redact_data(execution_plan_data(plan)).value
        return prepared_execution_plan_from_data(value)

    def _safe_result(self, result: AgentResult) -> AgentResult:
        value = self._redactor.redact_data(agent_result_data(result)).value
        return agent_result_from_data(value)


def _step_id(run_id: str, node_id: str, attempt: int) -> str:
    return hashlib.sha256(f"{run_id}\0{node_id}\0{attempt}".encode()).hexdigest()


def _fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_execution_json(value).encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("execution timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _datetime(row: aiosqlite.Row, name: str) -> datetime:
    return _utc(datetime.fromisoformat(str(row[name])))


def _optional_datetime(row: aiosqlite.Row, name: str) -> datetime | None:
    return None if row[name] is None else _datetime(row, name)
