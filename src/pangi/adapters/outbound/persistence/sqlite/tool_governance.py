"""SQLite persistence for Tool Policy versions and atomic Call Budgets."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.audit import SqliteAuditWriter
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.application.contracts.audit import AuditEventDraft
from pangi.application.contracts.tool_guardrails import ToolBudgetReservation, ToolPolicy
from pangi.application.contracts.tool_policy_persistence import (
    ToolPolicyActivation,
    ToolPolicyActivationCommand,
    ToolPolicyVersion,
)
from pangi.application.ports.tool_policy_persistence import (
    ToolBudgetPersistenceError,
    ToolPolicyConflictError,
    ToolPolicyPersistenceError,
    ToolPolicyStaleActivationError,
)
from pangi.application.services.audit import core_audit_redaction_service
from pangi.domain.audit import AuditOutcome
from pangi.domain.tool_guardrails import (
    ToolApprovalRequirement,
    ToolGuardrailErrorCode,
    ToolPermission,
    ToolPolicyEffect,
    ToolPolicyState,
)

Clock = Callable[[], datetime]

_POLICY_COLUMNS = """
    stable_tool_id,
    connection_id,
    policy_version,
    effect,
    permission,
    approval,
    schema_fingerprint,
    max_calls_per_run,
    max_argument_bytes,
    timeout_seconds,
    max_result_bytes,
    policy_fingerprint,
    state,
    created_at,
    updated_at
"""


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _policy_from_row(row: aiosqlite.Row) -> ToolPolicy:
    policy = ToolPolicy(
        policy_version=str(row["policy_version"]),
        tool_id=str(row["stable_tool_id"]),
        connection_id=str(row["connection_id"]),
        effect=ToolPolicyEffect(str(row["effect"])),
        permission=ToolPermission(str(row["permission"])),
        approval=ToolApprovalRequirement(str(row["approval"])),
        schema_fingerprint=str(row["schema_fingerprint"]),
        max_calls_per_run=int(row["max_calls_per_run"]),
        max_argument_bytes=int(row["max_argument_bytes"]),
        timeout_seconds=int(row["timeout_seconds"]),
        max_result_bytes=int(row["max_result_bytes"]),
    )
    if policy.fingerprint != str(row["policy_fingerprint"]):
        raise ValueError("persisted Tool Policy fingerprint does not match its fields")
    return policy


def _version_from_row(row: aiosqlite.Row) -> ToolPolicyVersion:
    return ToolPolicyVersion(
        policy=_policy_from_row(row),
        state=ToolPolicyState(str(row["state"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


class _SqliteToolRuntime:
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


class SqliteToolPolicyRepository(_SqliteToolRuntime):
    """Append immutable Tool Policy versions and load one exact active Policy."""

    def __init__(
        self,
        database: SqliteDatabase,
        audit_writer: SqliteAuditWriter | None = None,
    ) -> None:
        super().__init__(database)
        self._audit_writer = audit_writer or SqliteAuditWriter(
            core_audit_redaction_service()
        )

    async def save_draft(self, policy: ToolPolicy, *, at: datetime) -> None:
        if not isinstance(policy, ToolPolicy):
            raise TypeError("policy must be a ToolPolicy")
        timestamp = _utc(at, field_name="at").isoformat()
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "INSERT INTO tool_policies "
                    "(stable_tool_id, connection_id, policy_version, effect, permission, "
                    "approval, schema_fingerprint, max_calls_per_run, max_argument_bytes, "
                    "timeout_seconds, max_result_bytes, policy_fingerprint, state, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "'draft', ?, ?)",
                    (
                        policy.tool_id,
                        policy.connection_id,
                        policy.policy_version,
                        policy.effect.value,
                        policy.permission.value,
                        policy.approval.value,
                        policy.schema_fingerprint,
                        policy.max_calls_per_run,
                        policy.max_argument_bytes,
                        policy.timeout_seconds,
                        policy.max_result_bytes,
                        policy.fingerprint,
                        timestamp,
                        timestamp,
                    ),
                )
                await unit_of_work.commit()
        except aiosqlite.IntegrityError as error:
            raise ToolPolicyConflictError(
                "Tool Policy draft violated a persistence constraint"
            ) from error
        except aiosqlite.Error as error:
            raise ToolPolicyPersistenceError(
                "Tool Policy draft could not be persisted"
            ) from error

    async def get_policy(self, *, tool_id: str, connection_id: str) -> ToolPolicy | None:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                row = await fetch_one(
                    unit_of_work.connection,
                    f"SELECT {_POLICY_COLUMNS} FROM tool_policies "
                    "WHERE stable_tool_id = ? AND connection_id = ? AND state = 'active'",
                    (tool_id, connection_id),
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise ToolPolicyPersistenceError(
                "Active Tool Policy could not be loaded"
            ) from error
        if row is None:
            return None
        try:
            return _policy_from_row(row)
        except (KeyError, TypeError, ValueError) as error:
            raise ToolPolicyPersistenceError(
                "Persisted active Tool Policy is invalid"
            ) from error

    async def get_version(
        self,
        tool_id: str,
        policy_version: str,
    ) -> ToolPolicyVersion | None:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                row = await fetch_one(
                    unit_of_work.connection,
                    f"SELECT {_POLICY_COLUMNS} FROM tool_policies "
                    "WHERE stable_tool_id = ? AND policy_version = ?",
                    (tool_id, policy_version),
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise ToolPolicyPersistenceError(
                "Tool Policy version could not be loaded"
            ) from error
        if row is None:
            return None
        try:
            return _version_from_row(row)
        except (KeyError, TypeError, ValueError) as error:
            raise ToolPolicyPersistenceError(
                "Persisted Tool Policy version is invalid"
            ) from error

    async def activate(
        self,
        command: ToolPolicyActivationCommand,
    ) -> ToolPolicyActivation:
        if not isinstance(command, ToolPolicyActivationCommand):
            raise TypeError("command must be a ToolPolicyActivationCommand")
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                connection = unit_of_work.connection
                candidate_row = await fetch_one(
                    connection,
                    f"SELECT {_POLICY_COLUMNS} FROM tool_policies "
                    "WHERE stable_tool_id = ? AND policy_version = ?",
                    (command.tool_id, command.policy_version),
                )
                if candidate_row is None:
                    raise ToolPolicyConflictError("The candidate Tool Policy was not found")
                candidate = _version_from_row(candidate_row)
                if candidate.state is not ToolPolicyState.DRAFT:
                    raise ToolPolicyConflictError("Only a draft Tool Policy can be activated")
                if candidate.fingerprint != command.candidate_fingerprint:
                    raise ToolPolicyStaleActivationError(
                        "The candidate Tool Policy fingerprint changed"
                    )
                baseline_row = await fetch_one(
                    connection,
                    f"SELECT {_POLICY_COLUMNS} FROM tool_policies "
                    "WHERE stable_tool_id = ? AND state = 'active'",
                    (command.tool_id,),
                )
                baseline = None if baseline_row is None else _version_from_row(baseline_row)
                baseline_fingerprint = None if baseline is None else baseline.fingerprint
                if baseline_fingerprint != command.expected_active_fingerprint:
                    raise ToolPolicyStaleActivationError(
                        "The active Tool Policy changed"
                    )
                tool_row = await fetch_one(
                    connection,
                    "SELECT connection_id, permission, schema_fingerprint "
                    "FROM connection_tools WHERE stable_tool_id = ?",
                    (command.tool_id,),
                )
                if tool_row is None:
                    raise ToolPolicyConflictError("The candidate Tool is unavailable")
                policy = candidate.policy
                if (
                    str(tool_row["connection_id"]) != policy.connection_id
                    or str(tool_row["permission"]) != policy.permission.value
                    or str(tool_row["schema_fingerprint"])
                    != policy.schema_fingerprint
                ):
                    raise ToolPolicyStaleActivationError(
                        "The candidate Tool metadata changed"
                    )
                activated_at = command.activated_at.isoformat()
                if baseline is not None:
                    await connection.execute(
                        "UPDATE tool_policies SET state = 'retired', updated_at = ? "
                        "WHERE stable_tool_id = ? AND policy_version = ? AND state = 'active'",
                        (
                            activated_at,
                            baseline.policy.tool_id,
                            baseline.policy.policy_version,
                        ),
                    )
                cursor = await connection.execute(
                    "UPDATE tool_policies SET state = 'active', updated_at = ? "
                    "WHERE stable_tool_id = ? AND policy_version = ? AND state = 'draft'",
                    (activated_at, command.tool_id, command.policy_version),
                )
                try:
                    if cursor.rowcount != 1:
                        raise ToolPolicyConflictError(
                            "The candidate Tool Policy state changed"
                        )
                finally:
                    await cursor.close()
                await self._audit_writer.insert(
                    connection,
                    AuditEventDraft(
                        actor_id=command.actor_id,
                        action="tool_policy.version_activated",
                        resource_type="tool_policy",
                        resource_id=f"{command.tool_id}:{command.policy_version}",
                        outcome=AuditOutcome.SUCCEEDED,
                        created_at=command.activated_at,
                        before_summary=(
                            None
                            if baseline is None
                            else {
                                "fingerprint": baseline.fingerprint,
                                "policy_version": baseline.policy.policy_version,
                                "state": baseline.state.value,
                            }
                        ),
                        after_summary={
                            "fingerprint": candidate.fingerprint,
                            "policy_version": candidate.policy.policy_version,
                            "state": ToolPolicyState.ACTIVE.value,
                        },
                        details={
                            "approval": candidate.policy.approval.value,
                            "effect": candidate.policy.effect.value,
                            "permission": candidate.policy.permission.value,
                        },
                    ),
                )
                await unit_of_work.commit()
                active = ToolPolicyVersion(
                    policy=candidate.policy,
                    state=ToolPolicyState.ACTIVE,
                    created_at=candidate.created_at,
                    updated_at=command.activated_at,
                )
                return ToolPolicyActivation(active, baseline_fingerprint)
        except (ToolPolicyConflictError, ToolPolicyStaleActivationError):
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise ToolPolicyPersistenceError(
                "Persisted Tool Policy activation data is invalid"
            ) from error
        except aiosqlite.IntegrityError as error:
            raise ToolPolicyConflictError(
                "Tool Policy activation violated a persistence constraint"
            ) from error
        except aiosqlite.Error as error:
            raise ToolPolicyPersistenceError(
                "Tool Policy activation could not be persisted"
            ) from error


class SqliteToolBudgetLedger(_SqliteToolRuntime):
    """Atomically reserve durable Run·Tool calls after rechecking current policy."""

    def __init__(
        self,
        database: SqliteDatabase,
        *,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(database)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def reserve_call(
        self,
        *,
        run_id: str,
        tool_id: str,
        policy_fingerprint: str,
        max_calls_per_run: int,
    ) -> ToolBudgetReservation:
        if max_calls_per_run < 0:
            raise ValueError("max_calls_per_run cannot be negative")
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                connection = unit_of_work.connection
                budget_row = await fetch_one(
                    connection,
                    "SELECT calls_used, updated_at FROM tool_call_budgets "
                    "WHERE run_id = ? AND stable_tool_id = ?",
                    (run_id, tool_id),
                )
                calls_used = 0 if budget_row is None else int(budget_row["calls_used"])
                current = await fetch_one(
                    connection,
                    "SELECT ct.state AS tool_state, c.state AS connection_state, "
                    "tp.policy_fingerprint FROM connection_tools ct "
                    "JOIN connections c ON c.id = ct.connection_id "
                    "LEFT JOIN tool_policies tp ON tp.stable_tool_id = ct.stable_tool_id "
                    "AND tp.state = 'active' WHERE ct.stable_tool_id = ?",
                    (tool_id,),
                )
                if current is None or (
                    str(current["tool_state"]) != "active"
                    or str(current["connection_state"]) != "connected"
                ):
                    await unit_of_work.commit()
                    return ToolBudgetReservation(
                        False,
                        calls_used,
                        ToolGuardrailErrorCode.TOOL_UNAVAILABLE,
                    )
                if (
                    current["policy_fingerprint"] is None
                    or str(current["policy_fingerprint"]) != policy_fingerprint
                ):
                    await unit_of_work.commit()
                    return ToolBudgetReservation(
                        False,
                        calls_used,
                        ToolGuardrailErrorCode.POLICY_CHANGED,
                    )
                if calls_used >= max_calls_per_run:
                    await unit_of_work.commit()
                    return ToolBudgetReservation(
                        False,
                        calls_used,
                        ToolGuardrailErrorCode.CALL_BUDGET_EXCEEDED,
                    )
                timestamp = _utc(self._clock(), field_name="clock")
                if budget_row is None:
                    await connection.execute(
                        "INSERT INTO tool_call_budgets "
                        "(run_id, stable_tool_id, calls_used, last_policy_fingerprint, "
                        "created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?)",
                        (
                            run_id,
                            tool_id,
                            policy_fingerprint,
                            timestamp.isoformat(),
                            timestamp.isoformat(),
                        ),
                    )
                    calls_used = 1
                else:
                    previous_time = datetime.fromisoformat(str(budget_row["updated_at"]))
                    if timestamp <= previous_time:
                        timestamp = previous_time + timedelta(microseconds=1)
                    cursor = await connection.execute(
                        "UPDATE tool_call_budgets SET calls_used = calls_used + 1, "
                        "last_policy_fingerprint = ?, updated_at = ? "
                        "WHERE run_id = ? AND stable_tool_id = ? AND calls_used = ?",
                        (
                            policy_fingerprint,
                            timestamp.isoformat(),
                            run_id,
                            tool_id,
                            calls_used,
                        ),
                    )
                    try:
                        if cursor.rowcount != 1:
                            raise ToolBudgetPersistenceError(
                                "Tool Call Budget changed during reservation"
                            )
                    finally:
                        await cursor.close()
                    calls_used += 1
                await unit_of_work.commit()
                return ToolBudgetReservation(True, calls_used)
        except ToolBudgetPersistenceError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise ToolBudgetPersistenceError(
                "Persisted Tool Call Budget data is invalid"
            ) from error
        except aiosqlite.Error as error:
            raise ToolBudgetPersistenceError(
                "Tool Call Budget could not be reserved"
            ) from error
