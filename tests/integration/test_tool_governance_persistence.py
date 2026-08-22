"""Tool Policy, Schema validation, and durable Call Budget integration tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.connections import SqliteConnectionRegistry
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.tool_governance import (
    SqliteToolBudgetLedger,
    SqliteToolPolicyRepository,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.adapters.outbound.tool_arguments import JsonSchemaToolArgumentValidator
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.connections import ToolRegistrySnapshot
from pangi.application.contracts.tool_approval_persistence import (
    ToolApprovalConsumption,
    ToolApprovalExpectation,
)
from pangi.application.contracts.tool_guardrails import (
    GuardedToolCall,
    ToolCallRequest,
    ToolGuardrailBlockedError,
    ToolPolicy,
)
from pangi.application.contracts.tool_policy_persistence import (
    ToolPolicyActivationCommand,
)
from pangi.application.ports.tool_policy_persistence import (
    ToolPolicyConflictError,
    ToolPolicyStaleActivationError,
)
from pangi.application.services.connections import ToolRegistrySnapshotFactory
from pangi.application.services.tool_guardrails import (
    GuardedToolExecutionService,
    ToolGuardrailService,
)
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.connections import (
    Connection,
    ConnectionAuthType,
    ConnectionScope,
    ConnectionState,
    ConnectionToolState,
    ConnectionTransport,
)
from pangi.domain.tool_guardrails import (
    ToolApprovalRequirement,
    ToolGuardrailErrorCode,
    ToolGuardrailOutcome,
    ToolPermission,
    ToolPolicyEffect,
    ToolPolicyState,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)
USER_ID = "member-user-00001"
TOOL_ID = "linear.issue.create"
RUN_ID = "run-identifier-0001"


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


def _connection() -> Connection:
    return Connection(
        id="connection-user-0001",
        kind="linear",
        display_name="Linear",
        scope=ConnectionScope.USER,
        owner_user_id=USER_ID,
        transport=ConnectionTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.example.test",
        auth_type=ConnectionAuthType.OAUTH,
        secret_ref="secret://connection-user-0001/oauth",
        state=ConnectionState.CONNECTED,
        created_at=NOW,
        updated_at=NOW,
        connected_at=NOW,
        last_checked_at=NOW,
    )


def _snapshot(
    connection: Connection,
    *,
    input_schema: dict[str, object] | None = None,
    discovered_at: datetime = NOW,
    state: ConnectionToolState = ConnectionToolState.ACTIVE,
) -> ToolRegistrySnapshot:
    return ToolRegistrySnapshotFactory().build(
        connection=connection,
        stable_tool_id=TOOL_ID,
        remote_name="create_issue",
        permission=ToolPermission.WRITE,
        input_schema=input_schema
        or {
            "additionalProperties": False,
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "type": "object",
        },
        state=state,
        discovered_at=discovered_at,
    )


def _policy(
    snapshot: ToolRegistrySnapshot,
    *,
    version: str = "tool-policy-v1",
    max_calls_per_run: int = 2,
    permission: ToolPermission = ToolPermission.WRITE,
) -> ToolPolicy:
    return ToolPolicy(
        policy_version=version,
        tool_id=snapshot.stable_tool_id,
        connection_id=snapshot.connection_id,
        effect=ToolPolicyEffect.ALLOW,
        permission=permission,
        approval=ToolApprovalRequirement.NONE,
        schema_fingerprint=snapshot.schema_fingerprint,
        max_calls_per_run=max_calls_per_run,
        max_argument_bytes=1_024,
        timeout_seconds=30,
        max_result_bytes=4_096,
    )


async def _seed(
    database: SqliteDatabase,
    *,
    run_ids: tuple[str, ...] = (RUN_ID,),
) -> tuple[SqliteConnectionRegistry, Connection, ToolRegistrySnapshot]:
    async with database.create() as unit_of_work:
        timestamp = NOW.isoformat()
        await unit_of_work.connection.execute(
            "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
            "VALUES (?, 'Tool Governance Test', 'member', 'active', ?, ?)",
            (USER_ID, timestamp, timestamp),
        )
        for index, run_id in enumerate(run_ids):
            await unit_of_work.connection.execute(
                "INSERT INTO runs "
                "(id, request_id, principal_id, trigger, state, request_text, "
                "idempotency_key, created_at, updated_at) "
                "VALUES (?, ?, ?, 'eval', 'received', 'safe tool request', ?, ?, ?)",
                (
                    run_id,
                    f"tool-request-{index:04d}",
                    USER_ID,
                    f"tool-request-once-{index:04d}",
                    timestamp,
                    timestamp,
                ),
            )
        await unit_of_work.commit()
    registry = SqliteConnectionRegistry(database)
    connection = _connection()
    snapshot = _snapshot(connection)
    await registry.add_connection(connection)
    await registry.save_tool_snapshot(snapshot)
    return registry, connection, snapshot


def _activation(
    policy: ToolPolicy,
    *,
    expected_active_fingerprint: str | None,
    at: datetime,
) -> ToolPolicyActivationCommand:
    return ToolPolicyActivationCommand(
        actor_id="admin-user-00001",
        tool_id=policy.tool_id,
        policy_version=policy.policy_version,
        candidate_fingerprint=policy.fingerprint,
        expected_active_fingerprint=expected_active_fingerprint,
        activated_at=at,
    )


async def _save_and_activate(
    repository: SqliteToolPolicyRepository,
    policy: ToolPolicy,
    *,
    draft_at: datetime,
    active_at: datetime,
    expected_active_fingerprint: str | None = None,
) -> None:
    await repository.save_draft(policy, at=draft_at)
    await repository.activate(
        _activation(
            policy,
            expected_active_fingerprint=expected_active_fingerprint,
            at=active_at,
        )
    )


def test_policy_versions_activate_with_cas_and_secret_safe_audit(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            _, _, snapshot = await _seed(database)
            repository = SqliteToolPolicyRepository(database)
            first = _policy(snapshot)
            await repository.save_draft(first, at=NOW)
            draft = await repository.get_version(first.tool_id, first.policy_version)
            assert draft is not None and draft.state is ToolPolicyState.DRAFT
            assert await repository.get_policy(
                tool_id=first.tool_id,
                connection_id=first.connection_id,
            ) is None

            activated = await repository.activate(
                _activation(
                    first,
                    expected_active_fingerprint=None,
                    at=NOW + timedelta(seconds=1),
                )
            )
            assert activated.version.state is ToolPolicyState.ACTIVE
            assert activated.previous_active_fingerprint is None
            assert await repository.get_policy(
                tool_id=first.tool_id,
                connection_id=first.connection_id,
            ) == first
            assert await repository.get_policy(
                tool_id=first.tool_id,
                connection_id="connection-other-0001",
            ) is None

            second = _policy(snapshot, version="tool-policy-v2", max_calls_per_run=3)
            await repository.save_draft(second, at=NOW + timedelta(seconds=2))
            replaced = await repository.activate(
                _activation(
                    second,
                    expected_active_fingerprint=first.fingerprint,
                    at=NOW + timedelta(seconds=3),
                )
            )
            assert replaced.previous_active_fingerprint == first.fingerprint
            old = await repository.get_version(first.tool_id, first.policy_version)
            assert old is not None and old.state is ToolPolicyState.RETIRED
            assert await repository.get_policy(
                tool_id=second.tool_id,
                connection_id=second.connection_id,
            ) == second

            async with database.create() as unit_of_work:
                cursor = await unit_of_work.connection.execute(
                    "SELECT metadata_json FROM audit_events "
                    "WHERE action = 'tool_policy.version_activated' ORDER BY created_at"
                )
                rows = list(await cursor.fetchall())
                await cursor.close()
                await unit_of_work.commit()
            assert len(rows) == 2
            metadata = " ".join(str(row[0]) for row in rows)
            assert "secret://" not in metadata
            assert "connection-user-0001" not in metadata
            assert snapshot.canonical_schema_json not in metadata
            assert "safe tool request" not in metadata
        finally:
            await database.close()

    asyncio.run(scenario())


def test_policy_activation_rejects_stale_baseline_and_tool_drift(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            _, _, snapshot = await _seed(database)
            repository = SqliteToolPolicyRepository(database)
            first = _policy(snapshot)
            await _save_and_activate(
                repository,
                first,
                draft_at=NOW,
                active_at=NOW + timedelta(seconds=1),
            )
            stale = _policy(snapshot, version="tool-policy-v2")
            await repository.save_draft(stale, at=NOW + timedelta(seconds=2))
            with pytest.raises(ToolPolicyStaleActivationError):
                await repository.activate(
                    _activation(
                        stale,
                        expected_active_fingerprint=None,
                        at=NOW + timedelta(seconds=3),
                    )
                )
            drifted = _policy(
                snapshot,
                version="tool-policy-v3",
                permission=ToolPermission.READ,
            )
            await repository.save_draft(drifted, at=NOW + timedelta(seconds=4))
            with pytest.raises(ToolPolicyStaleActivationError):
                await repository.activate(
                    _activation(
                        drifted,
                        expected_active_fingerprint=first.fingerprint,
                        at=NOW + timedelta(seconds=5),
                    )
                )
            with pytest.raises(ToolPolicyConflictError):
                await repository.save_draft(first, at=NOW + timedelta(seconds=6))
        finally:
            await database.close()

    asyncio.run(scenario())


def test_budget_is_atomic_persistent_and_rechecks_active_policy(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            run_ids = (RUN_ID, "run-identifier-0002")
            registry, connection, snapshot = await _seed(database, run_ids=run_ids)
            repository = SqliteToolPolicyRepository(database)
            first = _policy(snapshot)
            await _save_and_activate(
                repository,
                first,
                draft_at=NOW,
                active_at=NOW + timedelta(seconds=1),
            )
            ledger = SqliteToolBudgetLedger(database, clock=lambda: NOW)
            first_call = await ledger.reserve_call(
                run_id=RUN_ID,
                tool_id=TOOL_ID,
                policy_fingerprint=first.fingerprint,
                max_calls_per_run=2,
            )
            second_call = await ledger.reserve_call(
                run_id=RUN_ID,
                tool_id=TOOL_ID,
                policy_fingerprint=first.fingerprint,
                max_calls_per_run=2,
            )
            exceeded = await ledger.reserve_call(
                run_id=RUN_ID,
                tool_id=TOOL_ID,
                policy_fingerprint=first.fingerprint,
                max_calls_per_run=2,
            )
            assert (first_call.calls_used, second_call.calls_used) == (1, 2)
            assert not exceeded.allowed
            assert exceeded.calls_used == 2
            assert exceeded.rejection_code is ToolGuardrailErrorCode.CALL_BUDGET_EXCEEDED

            second = _policy(snapshot, version="tool-policy-v2", max_calls_per_run=3)
            await _save_and_activate(
                repository,
                second,
                draft_at=NOW + timedelta(seconds=2),
                active_at=NOW + timedelta(seconds=3),
                expected_active_fingerprint=first.fingerprint,
            )
            stale_policy = await ledger.reserve_call(
                run_id=RUN_ID,
                tool_id=TOOL_ID,
                policy_fingerprint=first.fingerprint,
                max_calls_per_run=3,
            )
            assert not stale_policy.allowed
            assert stale_policy.calls_used == 2
            assert stale_policy.rejection_code is ToolGuardrailErrorCode.POLICY_CHANGED
            third_call = await ledger.reserve_call(
                run_id=RUN_ID,
                tool_id=TOOL_ID,
                policy_fingerprint=second.fingerprint,
                max_calls_per_run=3,
            )
            assert third_call.allowed and third_call.calls_used == 3

            parallel = await asyncio.gather(
                *(
                    ledger.reserve_call(
                        run_id=run_ids[1],
                        tool_id=TOOL_ID,
                        policy_fingerprint=second.fingerprint,
                        max_calls_per_run=3,
                    )
                    for _ in range(10)
                )
            )
            assert sum(result.allowed for result in parallel) == 3
            assert max(result.calls_used for result in parallel) == 3

            unavailable = _snapshot(
                connection,
                discovered_at=NOW + timedelta(seconds=4),
                state=ConnectionToolState.UNAVAILABLE,
            )
            await registry.save_tool_snapshot(unavailable)
            blocked = await ledger.reserve_call(
                run_id=run_ids[1],
                tool_id=TOOL_ID,
                policy_fingerprint=second.fingerprint,
                max_calls_per_run=4,
            )
            assert not blocked.allowed
            assert blocked.calls_used == 3
            assert blocked.rejection_code is ToolGuardrailErrorCode.TOOL_UNAVAILABLE
        finally:
            await database.close()

    asyncio.run(scenario())


class NoApprovalVerifier:
    async def consume_approval(
        self,
        approval_reference: str,
        *,
        expectation: ToolApprovalExpectation,
    ) -> ToolApprovalConsumption:
        return ToolApprovalConsumption.invalid()


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[GuardedToolCall] = []

    async def execute(self, call: GuardedToolCall) -> object:
        self.calls.append(call)
        return {"ok": True}


def test_persistent_adapters_complete_guardrail_before_execution(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            registry, _, snapshot = await _seed(database)
            policies = SqliteToolPolicyRepository(database)
            policy = _policy(snapshot)
            await _save_and_activate(
                policies,
                policy,
                draft_at=NOW,
                active_at=NOW + timedelta(seconds=1),
            )
            executor = RecordingExecutor()
            execution = GuardedToolExecutionService(
                ToolGuardrailService(
                    resolver=registry,
                    policy_provider=policies,
                    argument_validator=JsonSchemaToolArgumentValidator(registry),
                    approval_consumer=NoApprovalVerifier(),
                    budget_ledger=SqliteToolBudgetLedger(database, clock=lambda: NOW),
                    clock=lambda: NOW,
                ),
                executor=executor,
            )
            actor = AuthenticatedPrincipal(
                USER_ID,
                "Actor",
                UserRole.MEMBER,
                UserStatus.ACTIVE,
            )
            with pytest.raises(ToolGuardrailBlockedError) as captured:
                await execution.execute(
                    actor=actor,
                    request=ToolCallRequest(
                        run_id=RUN_ID,
                        principal_user_id=USER_ID,
                        tool_id=TOOL_ID,
                        arguments={"unexpected": True},
                    ),
                )
            assert captured.value.code is ToolGuardrailErrorCode.ARGUMENT_SCHEMA_INVALID
            assert executor.calls == []

            allowed = await execution.execute(
                actor=actor,
                request=ToolCallRequest(
                    run_id=RUN_ID,
                    principal_user_id=USER_ID,
                    tool_id=TOOL_ID,
                    arguments={"title": "새 이슈"},
                ),
            )
            assert allowed.decision.outcome is ToolGuardrailOutcome.ALLOWED
            assert len(executor.calls) == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_schema_validator_allows_local_refs_and_rejects_remote_refs(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            registry, connection, _ = await _seed(database)
            validator = JsonSchemaToolArgumentValidator(registry, max_cached_schemas=2)
            local = _snapshot(
                connection,
                input_schema={
                    "$defs": {"title": {"minLength": 1, "type": "string"}},
                    "properties": {"title": {"$ref": "#/$defs/title"}},
                    "required": ["title"],
                    "type": "object",
                },
                discovered_at=NOW + timedelta(seconds=1),
            )
            await registry.save_tool_snapshot(local)
            resolved = await registry.resolve(TOOL_ID)
            assert resolved is not None
            assert await validator.validate_arguments(
                tool=resolved,
                canonical_arguments_json='{"title":"ok"}',
            )
            assert not await validator.validate_arguments(
                tool=resolved,
                canonical_arguments_json='{"title":""}',
            )

            remote = _snapshot(
                connection,
                input_schema={"$ref": "https://schemas.example.test/tool.json"},
                discovered_at=NOW + timedelta(seconds=2),
            )
            await registry.save_tool_snapshot(remote)
            resolved = await registry.resolve(TOOL_ID)
            assert resolved is not None
            assert not await validator.validate_arguments(
                tool=resolved,
                canonical_arguments_json="{}",
            )
            assert not await validator.validate_arguments(
                tool=replace(resolved, schema_fingerprint="f" * 64),
                canonical_arguments_json="{}",
            )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_migration_constraints_reject_invalid_policy_and_budget_rows(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            _, _, snapshot = await _seed(database)
            policy = _policy(snapshot)
            values = (
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
                NOW.isoformat(),
                NOW.isoformat(),
            )
            insert = (
                "INSERT INTO tool_policies "
                "(stable_tool_id, connection_id, policy_version, effect, permission, "
                "approval, schema_fingerprint, max_calls_per_run, max_argument_bytes, "
                "timeout_seconds, max_result_bytes, policy_fingerprint, state, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'active', ?, ?)"
            )
            async with database.create() as unit_of_work:
                with pytest.raises(aiosqlite.IntegrityError):
                    await unit_of_work.connection.execute(insert, values)
            draft_insert = insert.replace("'active'", "'draft'")
            for index, invalid_value in (
                (4, "owner"),
                (7, -1),
                (11, "G" * 64),
            ):
                invalid_values = list(values)
                invalid_values[index] = invalid_value
                async with database.create() as unit_of_work:
                    with pytest.raises(aiosqlite.IntegrityError):
                        await unit_of_work.connection.execute(
                            draft_insert,
                            tuple(invalid_values),
                        )
            async with database.create() as unit_of_work:
                with pytest.raises(aiosqlite.IntegrityError):
                    await unit_of_work.connection.execute(
                        "INSERT INTO tool_call_budgets "
                        "(run_id, stable_tool_id, calls_used, last_policy_fingerprint, "
                        "created_at, updated_at) VALUES (?, ?, 2, ?, ?, ?)",
                        (
                            RUN_ID,
                            TOOL_ID,
                            policy.fingerprint,
                            NOW.isoformat(),
                            NOW.isoformat(),
                        ),
                    )
        finally:
            await database.close()

    asyncio.run(scenario())
