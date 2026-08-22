"""Tool Approval issuance and atomic consumption SQLite integration tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.audit import SqliteAuditWriter
from pangi.adapters.outbound.persistence.sqlite.connections import SqliteConnectionRegistry
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.tool_approvals import (
    SqliteToolApprovalStore,
)
from pangi.adapters.outbound.persistence.sqlite.tool_governance import (
    SqliteToolBudgetLedger,
    SqliteToolPolicyRepository,
)
from pangi.adapters.outbound.persistence.sqlite.tool_invocations import (
    SqliteToolInvocationRecorder,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.adapters.outbound.tool_arguments import JsonSchemaToolArgumentValidator
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.connections import ToolRegistrySnapshot
from pangi.application.contracts.tool_approval_persistence import (
    ToolApprovalExpectation,
    ToolApprovalIssuancePolicy,
    ToolApprovalIssueCommand,
)
from pangi.application.contracts.tool_guardrails import (
    GuardedToolCall,
    ToolCallRequest,
    ToolGuardrailBlockedError,
    ToolPolicy,
)
from pangi.application.contracts.tool_invocation_persistence import ToolInvocationContext
from pangi.application.contracts.tool_policy_persistence import (
    ToolPolicyActivationCommand,
)
from pangi.application.ports.tool_approval_persistence import (
    ToolApprovalIssueDeniedError,
    ToolApprovalPersistenceError,
)
from pangi.application.services.audit import core_audit_redaction_service
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
    ToolApprovalConsumptionStatus,
    ToolApprovalRequirement,
    ToolGuardrailErrorCode,
    ToolPermission,
    ToolPolicyEffect,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)
USER_ID = "member-user-00001"
ADMIN_ID = "admin-user-000001"
OTHER_ID = "member-user-00002"
RUN_ID = "run-identifier-0001"
TOOL_ID = "linear.issue.create"
REFERENCE = "approval-reference-secret-value-00001"
ARGUMENTS = {"title": "새 이슈"}


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


def _arguments_fingerprint(arguments: dict[str, object] = ARGUMENTS) -> str:
    canonical = json.dumps(
        arguments,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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


def _snapshot(connection: Connection) -> ToolRegistrySnapshot:
    return ToolRegistrySnapshotFactory().build(
        connection=connection,
        stable_tool_id=TOOL_ID,
        remote_name="create_issue",
        permission=ToolPermission.WRITE,
        input_schema={
            "additionalProperties": False,
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "type": "object",
        },
        state=ConnectionToolState.ACTIVE,
        discovered_at=NOW,
    )


def _policy(
    snapshot: ToolRegistrySnapshot,
    *,
    version: str = "tool-policy-v1",
    approval: ToolApprovalRequirement = ToolApprovalRequirement.USER,
    max_calls_per_run: int = 3,
) -> ToolPolicy:
    return ToolPolicy(
        policy_version=version,
        tool_id=snapshot.stable_tool_id,
        connection_id=snapshot.connection_id,
        effect=ToolPolicyEffect.ALLOW,
        permission=ToolPermission.WRITE,
        approval=approval,
        schema_fingerprint=snapshot.schema_fingerprint,
        max_calls_per_run=max_calls_per_run,
        max_argument_bytes=1_024,
        timeout_seconds=30,
        max_result_bytes=4_096,
    )


async def _seed(
    database: SqliteDatabase,
) -> tuple[SqliteConnectionRegistry, ToolRegistrySnapshot]:
    async with database.create() as unit_of_work:
        timestamp = NOW.isoformat()
        for user_id, display_name, role in (
            (USER_ID, "Member", "member"),
            (ADMIN_ID, "Admin", "admin"),
            (OTHER_ID, "Other", "member"),
        ):
            await unit_of_work.connection.execute(
                "INSERT INTO users "
                "(id, display_name, role, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'active', ?, ?)",
                (user_id, display_name, role, timestamp, timestamp),
            )
        await unit_of_work.connection.execute(
            "INSERT INTO runs "
            "(id, request_id, principal_id, trigger, state, request_text, "
            "idempotency_key, created_at, updated_at) "
            "VALUES (?, 'approval-request-0001', ?, 'eval', 'received', "
            "'safe tool request', 'approval-once-0001', ?, ?)",
            (RUN_ID, USER_ID, timestamp, timestamp),
        )
        await unit_of_work.commit()
    registry = SqliteConnectionRegistry(database)
    connection = _connection()
    snapshot = _snapshot(connection)
    await registry.add_connection(connection)
    await registry.save_tool_snapshot(snapshot)
    return registry, snapshot


async def _activate(
    repository: SqliteToolPolicyRepository,
    policy: ToolPolicy,
    *,
    at: datetime,
    expected: str | None = None,
) -> None:
    await repository.save_draft(policy, at=at - timedelta(seconds=1))
    await repository.activate(
        ToolPolicyActivationCommand(
            actor_id=ADMIN_ID,
            tool_id=policy.tool_id,
            policy_version=policy.policy_version,
            candidate_fingerprint=policy.fingerprint,
            expected_active_fingerprint=expected,
            activated_at=at,
        )
    )


def _store(
    database: SqliteDatabase,
    *,
    grant_id: str = "approval-grant-00001",
    reference: str = REFERENCE,
) -> SqliteToolApprovalStore:
    return SqliteToolApprovalStore(
        database,
        ToolApprovalIssuancePolicy(max_ttl_seconds=600),
        id_factory=lambda: grant_id,
        reference_factory=lambda: reference,
    )


def _issue_command(
    policy: ToolPolicy,
    *,
    requirement: ToolApprovalRequirement | None = None,
    subject_user_id: str = USER_ID,
    approver_user_id: str = USER_ID,
    approver_role: UserRole = UserRole.MEMBER,
    issued_at: datetime = NOW + timedelta(seconds=2),
    expires_at: datetime = NOW + timedelta(minutes=5),
) -> ToolApprovalIssueCommand:
    return ToolApprovalIssueCommand(
        subject_user_id=subject_user_id,
        approver_user_id=approver_user_id,
        approver_role=approver_role,
        run_id=RUN_ID,
        tool_id=TOOL_ID,
        arguments_fingerprint=_arguments_fingerprint(),
        policy_fingerprint=policy.fingerprint,
        approval_requirement=requirement or policy.approval,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _expectation(
    policy: ToolPolicy,
    *,
    arguments_fingerprint: str | None = None,
    consumed_at: datetime = NOW + timedelta(seconds=3),
) -> ToolApprovalExpectation:
    return ToolApprovalExpectation(
        subject_user_id=USER_ID,
        run_id=RUN_ID,
        tool_id=TOOL_ID,
        arguments_fingerprint=arguments_fingerprint or _arguments_fingerprint(),
        policy_fingerprint=policy.fingerprint,
        approval_requirement=policy.approval,
        consumed_at=consumed_at,
    )


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[GuardedToolCall] = []

    async def execute(self, call: GuardedToolCall) -> object:
        self.calls.append(call)
        return {"ok": True}


def test_user_grant_is_hashed_consumed_once_and_propagated_to_guarded_call(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            registry, snapshot = await _seed(database)
            policies = SqliteToolPolicyRepository(database)
            policy = _policy(snapshot)
            await _activate(policies, policy, at=NOW + timedelta(seconds=1))
            approvals = _store(database)
            issued = await approvals.issue_grant(_issue_command(policy))

            assert issued.reference == REFERENCE
            rendered = repr(issued)
            assert REFERENCE not in rendered
            assert USER_ID not in rendered
            assert RUN_ID not in rendered

            executor = RecordingExecutor()
            execution = GuardedToolExecutionService(
                ToolGuardrailService(
                    resolver=registry,
                    policy_provider=policies,
                    argument_validator=JsonSchemaToolArgumentValidator(registry),
                    approval_consumer=approvals,
                    budget_ledger=SqliteToolBudgetLedger(
                        database,
                        clock=lambda: NOW + timedelta(seconds=3),
                    ),
                    clock=lambda: NOW + timedelta(seconds=3),
                ),
                executor=executor,
                invocations=SqliteToolInvocationRecorder(database),
                clock=lambda: NOW + timedelta(seconds=3),
            )
            actor = AuthenticatedPrincipal(
                USER_ID,
                "Member",
                UserRole.MEMBER,
                UserStatus.ACTIVE,
            )
            result = await execution.execute(
                actor=actor,
                request=ToolCallRequest(
                    run_id=RUN_ID,
                    principal_user_id=USER_ID,
                    tool_id=TOOL_ID,
                    arguments=ARGUMENTS,
                    approval_reference=issued.reference,
                ),
                context=ToolInvocationContext(RUN_ID),
            )
            assert result.result == {"ok": True}
            assert executor.calls[0].approval_grant_id == issued.grant.grant_id

            with pytest.raises(ToolGuardrailBlockedError) as captured:
                await execution.execute(
                    actor=actor,
                    request=ToolCallRequest(
                        run_id=RUN_ID,
                        principal_user_id=USER_ID,
                        tool_id=TOOL_ID,
                        arguments=ARGUMENTS,
                        approval_reference=issued.reference,
                    ),
                    context=ToolInvocationContext(RUN_ID),
                )
            assert captured.value.code is ToolGuardrailErrorCode.APPROVAL_INVALID
            assert len(executor.calls) == 1

            async with database.create() as unit_of_work:
                cursor = await unit_of_work.connection.execute(
                    "SELECT reference_hash, state, consumed_at FROM tool_approvals"
                )
                row = await cursor.fetchone()
                await cursor.close()
                audit_cursor = await unit_of_work.connection.execute(
                    "SELECT metadata_json FROM audit_events "
                    "WHERE action LIKE 'tool_approval.%' ORDER BY created_at"
                )
                audit_rows = await audit_cursor.fetchall()
                await audit_cursor.close()
                await unit_of_work.commit()
            assert row is not None
            assert str(row["reference_hash"]) == hashlib.sha256(
                REFERENCE.encode("utf-8")
            ).hexdigest()
            assert str(row["state"]) == "consumed"
            assert row["consumed_at"] is not None
            persisted = " ".join(str(item["metadata_json"]) for item in audit_rows)
            assert len(audit_rows) == 2
            assert REFERENCE not in persisted
            assert "secret://" not in persisted
        finally:
            await database.close()

    asyncio.run(scenario())


def test_claim_mismatch_and_expiry_do_not_consume_the_grant(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            _, snapshot = await _seed(database)
            policies = SqliteToolPolicyRepository(database)
            policy = _policy(snapshot)
            await _activate(policies, policy, at=NOW + timedelta(seconds=1))
            approvals = _store(database)
            issued = await approvals.issue_grant(_issue_command(policy))

            mismatched = await approvals.consume_approval(
                issued.reference,
                expectation=_expectation(
                    policy,
                    arguments_fingerprint="f" * 64,
                ),
            )
            assert mismatched.status is ToolApprovalConsumptionStatus.INVALID

            async with database.create() as unit_of_work:
                cursor = await unit_of_work.connection.execute(
                    "SELECT state FROM tool_approvals WHERE id = ?",
                    (issued.grant.grant_id,),
                )
                row = await cursor.fetchone()
                await cursor.close()
                await unit_of_work.commit()
            assert row is not None and str(row["state"]) == "active"

            expired = await approvals.consume_approval(
                issued.reference,
                expectation=_expectation(
                    policy,
                    consumed_at=NOW + timedelta(minutes=5),
                ),
            )
            assert expired.status is ToolApprovalConsumptionStatus.EXPIRED

            valid = await approvals.consume_approval(
                issued.reference,
                expectation=_expectation(policy),
            )
            assert valid.status is ToolApprovalConsumptionStatus.CONSUMED
        finally:
            await database.close()

    asyncio.run(scenario())


def test_parallel_consumption_allows_exactly_one_caller(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            _, snapshot = await _seed(database)
            policies = SqliteToolPolicyRepository(database)
            policy = _policy(snapshot)
            await _activate(policies, policy, at=NOW + timedelta(seconds=1))
            approvals = _store(database)
            issued = await approvals.issue_grant(_issue_command(policy))

            results = await asyncio.gather(
                *(
                    approvals.consume_approval(
                        issued.reference,
                        expectation=_expectation(policy),
                    )
                    for _ in range(10)
                )
            )
            assert sum(
                result.status is ToolApprovalConsumptionStatus.CONSUMED
                for result in results
            ) == 1
            assert sum(
                result.status is ToolApprovalConsumptionStatus.INVALID
                for result in results
            ) == 9
        finally:
            await database.close()

    asyncio.run(scenario())


def test_consumed_grant_is_not_refunded_when_budget_blocks(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            registry, snapshot = await _seed(database)
            policies = SqliteToolPolicyRepository(database)
            policy = _policy(snapshot, max_calls_per_run=0)
            await _activate(policies, policy, at=NOW + timedelta(seconds=1))
            approvals = _store(database)
            issued = await approvals.issue_grant(_issue_command(policy))
            executor = RecordingExecutor()
            execution = GuardedToolExecutionService(
                ToolGuardrailService(
                    resolver=registry,
                    policy_provider=policies,
                    argument_validator=JsonSchemaToolArgumentValidator(registry),
                    approval_consumer=approvals,
                    budget_ledger=SqliteToolBudgetLedger(database),
                    clock=lambda: NOW + timedelta(seconds=3),
                ),
                executor=executor,
                invocations=SqliteToolInvocationRecorder(database),
                clock=lambda: NOW + timedelta(seconds=3),
            )
            actor = AuthenticatedPrincipal(
                USER_ID,
                "Member",
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
                        arguments=ARGUMENTS,
                        approval_reference=issued.reference,
                    ),
                    context=ToolInvocationContext(RUN_ID),
                )
            assert captured.value.code is ToolGuardrailErrorCode.CALL_BUDGET_EXCEEDED
            assert executor.calls == []
            reused = await approvals.consume_approval(
                issued.reference,
                expectation=_expectation(policy),
            )
            assert reused.status is ToolApprovalConsumptionStatus.INVALID
        finally:
            await database.close()

    asyncio.run(scenario())


def test_issuance_and_consumption_recheck_identity_run_and_policy(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            _, snapshot = await _seed(database)
            policies = SqliteToolPolicyRepository(database)
            admin_policy = _policy(
                snapshot,
                approval=ToolApprovalRequirement.ADMIN,
            )
            await _activate(policies, admin_policy, at=NOW + timedelta(seconds=1))
            approvals = _store(database)

            with pytest.raises(ToolApprovalIssueDeniedError):
                await approvals.issue_grant(
                    _issue_command(
                        admin_policy,
                        approver_user_id=USER_ID,
                        approver_role=UserRole.MEMBER,
                    )
                )
            with pytest.raises(ToolApprovalIssueDeniedError):
                await approvals.issue_grant(
                    _issue_command(
                        admin_policy,
                        approver_user_id=ADMIN_ID,
                        approver_role=UserRole.ADMIN,
                        expires_at=NOW + timedelta(minutes=20),
                    )
                )
            with pytest.raises(ToolApprovalIssueDeniedError):
                await approvals.issue_grant(
                    _issue_command(
                        admin_policy,
                        subject_user_id=OTHER_ID,
                        approver_user_id=ADMIN_ID,
                        approver_role=UserRole.ADMIN,
                    )
                )

            issued = await approvals.issue_grant(
                _issue_command(
                    admin_policy,
                    approver_user_id=ADMIN_ID,
                    approver_role=UserRole.ADMIN,
                )
            )
            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "UPDATE users SET role = 'member', updated_at = ? WHERE id = ?",
                    ((NOW + timedelta(seconds=3)).isoformat(), ADMIN_ID),
                )
                await unit_of_work.commit()
            demoted = await approvals.consume_approval(
                issued.reference,
                expectation=_expectation(admin_policy),
            )
            assert demoted.status is ToolApprovalConsumptionStatus.INVALID

            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "UPDATE users SET role = 'admin', updated_at = ? WHERE id = ?",
                    ((NOW + timedelta(seconds=4)).isoformat(), ADMIN_ID),
                )
                await unit_of_work.commit()
            replacement = _policy(
                snapshot,
                version="tool-policy-v2",
                approval=ToolApprovalRequirement.ADMIN,
            )
            await _activate(
                policies,
                replacement,
                at=NOW + timedelta(seconds=6),
                expected=admin_policy.fingerprint,
            )
            changed = await approvals.consume_approval(
                issued.reference,
                expectation=_expectation(admin_policy),
            )
            assert changed.status is ToolApprovalConsumptionStatus.INVALID

            fresh_store = _store(
                database,
                grant_id="approval-grant-00002",
                reference="approval-reference-secret-value-00002",
            )
            fresh = await fresh_store.issue_grant(
                _issue_command(
                    replacement,
                    approver_user_id=ADMIN_ID,
                    approver_role=UserRole.ADMIN,
                    issued_at=NOW + timedelta(seconds=7),
                    expires_at=NOW + timedelta(minutes=6),
                )
            )

            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "UPDATE users SET status = 'disabled', updated_at = ? WHERE id = ?",
                    ((NOW + timedelta(seconds=8)).isoformat(), USER_ID),
                )
                await unit_of_work.commit()
            disabled = await fresh_store.consume_approval(
                fresh.reference,
                expectation=_expectation(
                    replacement,
                    consumed_at=NOW + timedelta(seconds=9),
                ),
            )
            assert disabled.status is ToolApprovalConsumptionStatus.INVALID
            with pytest.raises(ToolApprovalIssueDeniedError):
                await SqliteToolApprovalStore(
                    database,
                    ToolApprovalIssuancePolicy(max_ttl_seconds=600),
                    id_factory=lambda: "approval-grant-00003",
                    reference_factory=lambda: "approval-reference-secret-value-00003",
                ).issue_grant(
                    _issue_command(
                        replacement,
                        approver_user_id=ADMIN_ID,
                        approver_role=UserRole.ADMIN,
                        issued_at=NOW + timedelta(seconds=10),
                        expires_at=NOW + timedelta(minutes=6),
                    )
                )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_database_rejects_mutated_claims_and_invalid_state_transitions(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            _, snapshot = await _seed(database)
            policies = SqliteToolPolicyRepository(database)
            policy = _policy(snapshot)
            await _activate(policies, policy, at=NOW + timedelta(seconds=1))
            approvals = _store(database)
            issued = await approvals.issue_grant(_issue_command(policy))

            async with database.create() as unit_of_work:
                with pytest.raises(aiosqlite.IntegrityError):
                    await unit_of_work.connection.execute(
                        "UPDATE tool_approvals SET arguments_fingerprint = ? WHERE id = ?",
                        ("f" * 64, issued.grant.grant_id),
                    )
                await unit_of_work.rollback()
            async with database.create() as unit_of_work:
                with pytest.raises(aiosqlite.IntegrityError):
                    await unit_of_work.connection.execute(
                        "UPDATE tool_approvals SET state = 'consumed', consumed_at = ? "
                        "WHERE id = ?",
                        (
                            (NOW + timedelta(minutes=6)).isoformat(),
                            issued.grant.grant_id,
                        ),
                    )
                await unit_of_work.rollback()
        finally:
            await database.close()

    asyncio.run(scenario())


def test_consumption_rolls_back_when_audit_cannot_be_persisted(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            _, snapshot = await _seed(database)
            policies = SqliteToolPolicyRepository(database)
            policy = _policy(snapshot)
            await _activate(policies, policy, at=NOW + timedelta(seconds=1))
            audit_writer = SqliteAuditWriter(
                core_audit_redaction_service(),
                id_factory=lambda: "approval-audit-event-0001",
            )
            approvals = SqliteToolApprovalStore(
                database,
                ToolApprovalIssuancePolicy(max_ttl_seconds=600),
                audit_writer,
                id_factory=lambda: "approval-grant-00001",
                reference_factory=lambda: REFERENCE,
            )
            issued = await approvals.issue_grant(_issue_command(policy))

            with pytest.raises(ToolApprovalPersistenceError):
                await approvals.consume_approval(
                    issued.reference,
                    expectation=_expectation(policy),
                )

            async with database.create() as unit_of_work:
                cursor = await unit_of_work.connection.execute(
                    "SELECT state, consumed_at FROM tool_approvals WHERE id = ?",
                    (issued.grant.grant_id,),
                )
                row = await cursor.fetchone()
                await cursor.close()
                await unit_of_work.commit()
            assert row is not None
            assert str(row["state"]) == "active"
            assert row["consumed_at"] is None
        finally:
            await database.close()

    asyncio.run(scenario())
