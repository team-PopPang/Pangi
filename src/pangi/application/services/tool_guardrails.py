"""Deterministic Tool authorization and mandatory guarded execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from math import isfinite
from typing import NoReturn

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.tool_approval_persistence import (
    ToolApprovalExpectation,
)
from pangi.application.contracts.tool_guardrails import (
    ApprovalGrant,
    GuardedToolCall,
    GuardedToolExecution,
    ResolvedTool,
    ToolCallRequest,
    ToolExecutionLimits,
    ToolGuardrailBlockedError,
    ToolGuardrailDecision,
    ToolPolicy,
)
from pangi.application.ports.tool_guardrails import (
    StableToolResolver,
    ToolApprovalConsumer,
    ToolArgumentValidator,
    ToolBudgetLedger,
    ToolExecutor,
    ToolPolicyProvider,
)
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.tool_guardrails import (
    ToolApprovalConsumptionStatus,
    ToolApprovalRequirement,
    ToolConnectionScope,
    ToolGuardrailErrorCode,
    ToolGuardrailOutcome,
    ToolGuardrailStage,
    ToolPermission,
    ToolPolicyEffect,
)


def _normalize_json(value: object, active: set[int]) -> object:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("non-finite numbers are not JSON values")
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic mappings are not JSON values")
        active.add(identity)
        try:
            normalized: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                normalized[key] = _normalize_json(item, active)
            return normalized
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic sequences are not JSON values")
        active.add(identity)
        try:
            return [_normalize_json(item, active) for item in value]
        finally:
            active.remove(identity)
    raise TypeError("value is not JSON compatible")


def _canonical_arguments(arguments: Mapping[str, object]) -> tuple[str, int, str]:
    normalized = _normalize_json(arguments, set())
    canonical = json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    encoded = canonical.encode("utf-8")
    return canonical, len(encoded), hashlib.sha256(encoded).hexdigest()


class ToolGuardrailService:
    """Evaluate proposed Tool calls in a fixed, fail-closed order."""

    def __init__(
        self,
        *,
        resolver: StableToolResolver,
        policy_provider: ToolPolicyProvider,
        argument_validator: ToolArgumentValidator,
        approval_consumer: ToolApprovalConsumer,
        budget_ledger: ToolBudgetLedger,
        clock: Callable[[], datetime],
    ) -> None:
        self._resolver = resolver
        self._policy_provider = policy_provider
        self._argument_validator = argument_validator
        self._approval_consumer = approval_consumer
        self._budget_ledger = budget_ledger
        self._clock = clock

    async def guard(
        self,
        *,
        actor: AuthenticatedPrincipal,
        request: ToolCallRequest,
    ) -> GuardedToolCall:
        if actor.status is not UserStatus.ACTIVE:
            self._block(
                request,
                ToolGuardrailStage.PRINCIPAL,
                ToolGuardrailErrorCode.PRINCIPAL_INACTIVE,
            )
        if actor.user_id != request.principal_user_id:
            self._block(
                request,
                ToolGuardrailStage.PRINCIPAL,
                ToolGuardrailErrorCode.PRINCIPAL_ID_MISMATCH,
            )

        tool = await self._resolver.resolve(request.tool_id)
        if tool is None:
            self._block(
                request,
                ToolGuardrailStage.RESOLUTION,
                ToolGuardrailErrorCode.UNKNOWN_TOOL,
            )
        if tool.tool_id != request.tool_id:
            self._block(
                request,
                ToolGuardrailStage.RESOLUTION,
                ToolGuardrailErrorCode.UNKNOWN_TOOL,
            )
        if not tool.active:
            self._block(
                request,
                ToolGuardrailStage.RESOLUTION,
                ToolGuardrailErrorCode.TOOL_UNAVAILABLE,
                permission=tool.permission,
            )
        if (
            tool.connection_scope is ToolConnectionScope.USER
            and tool.connection_owner_user_id != actor.user_id
        ):
            self._block(
                request,
                ToolGuardrailStage.SCOPE,
                ToolGuardrailErrorCode.CONNECTION_SCOPE_DENIED,
                permission=tool.permission,
            )

        policy = await self._policy_provider.get_policy(
            tool_id=tool.tool_id,
            connection_id=tool.connection_id,
        )
        if (
            policy is None
            or policy.tool_id != tool.tool_id
            or policy.connection_id != tool.connection_id
        ):
            self._block(
                request,
                ToolGuardrailStage.POLICY,
                ToolGuardrailErrorCode.POLICY_MISSING,
                permission=tool.permission,
            )
        if policy.effect is ToolPolicyEffect.DENY:
            self._block(
                request,
                ToolGuardrailStage.POLICY,
                ToolGuardrailErrorCode.POLICY_DENIED,
                tool=tool,
                policy=policy,
            )
        if policy.permission is not tool.permission:
            self._block(
                request,
                ToolGuardrailStage.POLICY,
                ToolGuardrailErrorCode.PERMISSION_MISMATCH,
                tool=tool,
                policy=policy,
            )
        if policy.schema_fingerprint != tool.schema_fingerprint:
            self._block(
                request,
                ToolGuardrailStage.POLICY,
                ToolGuardrailErrorCode.SCHEMA_FINGERPRINT_MISMATCH,
                tool=tool,
                policy=policy,
            )

        try:
            canonical_arguments, argument_bytes, arguments_fingerprint = _canonical_arguments(
                request.arguments
            )
        except (RecursionError, TypeError, ValueError):
            self._block(
                request,
                ToolGuardrailStage.ARGUMENTS,
                ToolGuardrailErrorCode.ARGUMENTS_NOT_JSON,
                tool=tool,
                policy=policy,
            )
        if argument_bytes > policy.max_argument_bytes:
            self._block(
                request,
                ToolGuardrailStage.ARGUMENTS,
                ToolGuardrailErrorCode.ARGUMENT_BYTES_EXCEEDED,
                tool=tool,
                policy=policy,
                argument_bytes=argument_bytes,
            )
        if not await self._argument_validator.validate_arguments(
            tool=tool,
            canonical_arguments_json=canonical_arguments,
        ):
            self._block(
                request,
                ToolGuardrailStage.ARGUMENTS,
                ToolGuardrailErrorCode.ARGUMENT_SCHEMA_INVALID,
                tool=tool,
                policy=policy,
                argument_bytes=argument_bytes,
            )

        approval_grant_id = await self._guard_approval(
            actor=actor,
            request=request,
            tool=tool,
            policy=policy,
            arguments_fingerprint=arguments_fingerprint,
            argument_bytes=argument_bytes,
        )
        if policy.max_calls_per_run == 0:
            self._block(
                request,
                ToolGuardrailStage.BUDGET,
                ToolGuardrailErrorCode.CALL_BUDGET_EXCEEDED,
                tool=tool,
                policy=policy,
                argument_bytes=argument_bytes,
                calls_used=0,
            )
        reservation = await self._budget_ledger.reserve_call(
            run_id=request.run_id,
            tool_id=tool.tool_id,
            policy_fingerprint=policy.fingerprint,
            max_calls_per_run=policy.max_calls_per_run,
        )
        if not reservation.allowed or reservation.calls_used > policy.max_calls_per_run:
            rejection_code = (
                reservation.rejection_code
                or ToolGuardrailErrorCode.CALL_BUDGET_EXCEEDED
            )
            rejection_stage = (
                ToolGuardrailStage.POLICY
                if rejection_code is ToolGuardrailErrorCode.POLICY_CHANGED
                else ToolGuardrailStage.RESOLUTION
                if rejection_code is ToolGuardrailErrorCode.TOOL_UNAVAILABLE
                else ToolGuardrailStage.BUDGET
            )
            self._block(
                request,
                rejection_stage,
                rejection_code,
                tool=tool,
                policy=policy,
                argument_bytes=argument_bytes,
                calls_used=reservation.calls_used,
            )

        decision = self._decision(
            request,
            stage=ToolGuardrailStage.COMPLETE,
            outcome=ToolGuardrailOutcome.ALLOWED,
            tool=tool,
            policy=policy,
            argument_bytes=argument_bytes,
            calls_used=reservation.calls_used,
        )
        return GuardedToolCall(
            run_id=request.run_id,
            tool=tool,
            canonical_arguments_json=canonical_arguments,
            arguments_fingerprint=arguments_fingerprint,
            policy_fingerprint=policy.fingerprint,
            approval_grant_id=approval_grant_id,
            limits=ToolExecutionLimits(
                timeout_seconds=policy.timeout_seconds,
                max_result_bytes=policy.max_result_bytes,
            ),
            decision=decision,
        )

    async def _guard_approval(
        self,
        *,
        actor: AuthenticatedPrincipal,
        request: ToolCallRequest,
        tool: ResolvedTool,
        policy: ToolPolicy,
        arguments_fingerprint: str,
        argument_bytes: int,
    ) -> str | None:
        if policy.approval is ToolApprovalRequirement.NONE:
            return None
        if request.approval_reference is None:
            self._block(
                request,
                ToolGuardrailStage.APPROVAL,
                ToolGuardrailErrorCode.APPROVAL_REQUIRED,
                tool=tool,
                policy=policy,
                argument_bytes=argument_bytes,
            )
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Tool guardrail clock must return a timezone-aware datetime")
        consumed = await self._approval_consumer.consume_approval(
            request.approval_reference,
            expectation=ToolApprovalExpectation(
                subject_user_id=actor.user_id,
                run_id=request.run_id,
                tool_id=tool.tool_id,
                arguments_fingerprint=arguments_fingerprint,
                policy_fingerprint=policy.fingerprint,
                approval_requirement=policy.approval,
                consumed_at=now,
            ),
        )
        if consumed.status is ToolApprovalConsumptionStatus.EXPIRED:
            self._block(
                request,
                ToolGuardrailStage.APPROVAL,
                ToolGuardrailErrorCode.APPROVAL_EXPIRED,
                tool=tool,
                policy=policy,
                argument_bytes=argument_bytes,
            )
        grant = consumed.grant
        if (
            consumed.status is not ToolApprovalConsumptionStatus.CONSUMED
            or grant is None
        ):
            self._block(
                request,
                ToolGuardrailStage.APPROVAL,
                ToolGuardrailErrorCode.APPROVAL_INVALID,
                tool=tool,
                policy=policy,
                argument_bytes=argument_bytes,
            )
        if grant.expires_at <= now.astimezone(UTC) or not self._approval_matches(
            grant,
            actor=actor,
            request=request,
            tool=tool,
            policy=policy,
            arguments_fingerprint=arguments_fingerprint,
        ):
            self._block(
                request,
                ToolGuardrailStage.APPROVAL,
                ToolGuardrailErrorCode.APPROVAL_INVALID,
                tool=tool,
                policy=policy,
                argument_bytes=argument_bytes,
            )
        return grant.grant_id

    @staticmethod
    def _approval_matches(
        grant: ApprovalGrant,
        *,
        actor: AuthenticatedPrincipal,
        request: ToolCallRequest,
        tool: ResolvedTool,
        policy: ToolPolicy,
        arguments_fingerprint: str,
    ) -> bool:
        if (
            grant.subject_user_id != actor.user_id
            or grant.run_id != request.run_id
            or grant.tool_id != tool.tool_id
            or grant.arguments_fingerprint != arguments_fingerprint
            or grant.policy_fingerprint != policy.fingerprint
            or grant.approval_requirement is not policy.approval
        ):
            return False
        if policy.approval is ToolApprovalRequirement.USER:
            return grant.approver_user_id == actor.user_id
        return grant.approver_role is UserRole.ADMIN

    @staticmethod
    def _decision(
        request: ToolCallRequest,
        *,
        stage: ToolGuardrailStage,
        outcome: ToolGuardrailOutcome,
        tool: ResolvedTool | None = None,
        policy: ToolPolicy | None = None,
        permission: ToolPermission | None = None,
        argument_bytes: int | None = None,
        calls_used: int | None = None,
        error_code: ToolGuardrailErrorCode | None = None,
    ) -> ToolGuardrailDecision:
        resolved_permission = tool.permission if tool is not None else permission
        return ToolGuardrailDecision(
            tool_id=request.tool_id,
            stage=stage,
            outcome=outcome,
            policy_version=policy.policy_version if policy is not None else None,
            policy_fingerprint=policy.fingerprint if policy is not None else None,
            permission=resolved_permission,
            argument_bytes=argument_bytes,
            calls_used=calls_used,
            error_code=error_code,
        )

    def _block(
        self,
        request: ToolCallRequest,
        stage: ToolGuardrailStage,
        error_code: ToolGuardrailErrorCode,
        *,
        tool: ResolvedTool | None = None,
        policy: ToolPolicy | None = None,
        permission: ToolPermission | None = None,
        argument_bytes: int | None = None,
        calls_used: int | None = None,
    ) -> NoReturn:
        raise ToolGuardrailBlockedError(
            self._decision(
                request,
                stage=stage,
                outcome=ToolGuardrailOutcome.BLOCKED,
                tool=tool,
                policy=policy,
                permission=permission,
                argument_bytes=argument_bytes,
                calls_used=calls_used,
                error_code=error_code,
            )
        )


class GuardedToolExecutionService:
    """Make Tool authorization the mandatory boundary before execution."""

    def __init__(
        self,
        guardrail: ToolGuardrailService,
        *,
        executor: ToolExecutor,
    ) -> None:
        self._guardrail = guardrail
        self._executor = executor

    async def execute(
        self,
        *,
        actor: AuthenticatedPrincipal,
        request: ToolCallRequest,
    ) -> GuardedToolExecution:
        guarded = await self._guardrail.guard(actor=actor, request=request)
        result = await self._executor.execute(guarded)
        return GuardedToolExecution(result=result, decision=guarded.decision)
