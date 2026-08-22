"""Connection Registry integration with the WBS-06 Tool guardrail contract."""

import asyncio
from datetime import UTC, datetime

import pytest

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.connections import ToolRegistrySnapshot
from pangi.application.contracts.tool_approval_persistence import (
    ToolApprovalConsumption,
    ToolApprovalExpectation,
)
from pangi.application.contracts.tool_guardrails import (
    GuardedToolCall,
    ResolvedTool,
    ToolBudgetReservation,
    ToolCallRequest,
    ToolGuardrailBlockedError,
    ToolPolicy,
)
from pangi.application.contracts.tool_invocation_persistence import (
    ToolInvocationContext,
    ToolInvocationDenial,
    ToolInvocationFinish,
    ToolInvocationStart,
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
from pangi.domain.tool_guardrails import ToolGuardrailErrorCode, ToolPermission

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class SnapshotResolver:
    def __init__(self, snapshot: ToolRegistrySnapshot) -> None:
        self.snapshot = snapshot

    async def resolve(self, tool_id: str) -> ResolvedTool | None:
        assert tool_id == self.snapshot.stable_tool_id
        return self.snapshot.as_resolved_tool()


class MissingPolicyProvider:
    async def get_policy(self, *, tool_id: str, connection_id: str) -> ToolPolicy | None:
        return None


class NeverArgumentValidator:
    async def validate_arguments(
        self,
        *,
        tool: ResolvedTool,
        canonical_arguments_json: str,
    ) -> bool:
        raise AssertionError("an unavailable or ungoverned Tool cannot reach schema validation")


class NeverApprovalVerifier:
    async def consume_approval(
        self,
        approval_reference: str,
        *,
        expectation: ToolApprovalExpectation,
    ) -> ToolApprovalConsumption:
        raise AssertionError("an unavailable or ungoverned Tool cannot reach approval")


class NeverBudgetLedger:
    async def reserve_call(
        self,
        *,
        run_id: str,
        tool_id: str,
        policy_fingerprint: str,
        max_calls_per_run: int,
    ) -> ToolBudgetReservation:
        raise AssertionError("an unavailable or ungoverned Tool cannot reserve budget")


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[GuardedToolCall] = []

    async def execute(self, call: GuardedToolCall) -> object:
        self.calls.append(call)
        return "unexpected"


class NoopInvocationRecorder:
    async def start(self, invocation: ToolInvocationStart) -> None:
        del invocation

    async def deny(self, invocation: ToolInvocationDenial) -> None:
        del invocation

    async def finish(self, invocation: ToolInvocationFinish) -> None:
        del invocation


def _snapshot(state: ConnectionToolState) -> ToolRegistrySnapshot:
    connection = Connection(
        id="connection-user-0001",
        kind="linear",
        display_name="Linear",
        scope=ConnectionScope.USER,
        owner_user_id="member-user-00001",
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
    return ToolRegistrySnapshotFactory().build(
        connection=connection,
        stable_tool_id="linear.issue.create",
        remote_name="create_issue",
        permission=ToolPermission.WRITE,
        input_schema={"type": "object"},
        state=state,
        discovered_at=NOW,
    )


@pytest.mark.parametrize(
    ("registry_state", "expected_code"),
    (
        (ConnectionToolState.NEW, ToolGuardrailErrorCode.TOOL_UNAVAILABLE),
        (ConnectionToolState.CHANGED, ToolGuardrailErrorCode.TOOL_UNAVAILABLE),
        (ConnectionToolState.UNAVAILABLE, ToolGuardrailErrorCode.TOOL_UNAVAILABLE),
        (ConnectionToolState.ACTIVE, ToolGuardrailErrorCode.POLICY_MISSING),
    ),
)
def test_registry_state_and_missing_policy_fail_closed_before_execution(
    registry_state: ConnectionToolState,
    expected_code: ToolGuardrailErrorCode,
) -> None:
    snapshot = _snapshot(registry_state)
    executor = RecordingExecutor()
    service = GuardedToolExecutionService(
        ToolGuardrailService(
            resolver=SnapshotResolver(snapshot),
            policy_provider=MissingPolicyProvider(),
            argument_validator=NeverArgumentValidator(),
            approval_consumer=NeverApprovalVerifier(),
            budget_ledger=NeverBudgetLedger(),
            clock=lambda: NOW,
        ),
        executor=executor,
        invocations=NoopInvocationRecorder(),
        clock=lambda: NOW,
        id_factory=lambda: "tool-invocation-0001",
    )
    actor = AuthenticatedPrincipal(
        "member-user-00001",
        "Actor",
        UserRole.MEMBER,
        UserStatus.ACTIVE,
    )

    with pytest.raises(ToolGuardrailBlockedError) as captured:
        asyncio.run(
            service.execute(
                actor=actor,
                request=ToolCallRequest(
                    run_id="run-identifier-0001",
                    principal_user_id=actor.user_id,
                    tool_id=snapshot.stable_tool_id,
                    arguments={"title": "새 이슈"},
                ),
                context=ToolInvocationContext("run-identifier-0001"),
            )
        )

    assert captured.value.code is expected_code
    assert executor.calls == []
