"""Deterministic Model Egress decisions and mandatory redacted execution."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import NoReturn

from pangi.application.contracts.model_persistence import (
    ModelInvocationContext,
    ModelInvocationDenial,
    ModelInvocationFinish,
    ModelInvocationStart,
    logical_call_fingerprint,
)
from pangi.application.contracts.model_routing import (
    GuardedModelExecution,
    GuardedModelRequest,
    ModelCallRequest,
    ModelEgressPolicy,
    ModelInputSource,
    ModelPolicyBlockedError,
    ModelPolicyDecision,
    ModelProfile,
    ModelProviderFailure,
    ModelProviderResponse,
)
from pangi.application.contracts.redaction import RedactionInputError, RedactionSummary
from pangi.application.ports.model_persistence import ModelInvocationRecorder
from pangi.application.ports.model_routing import (
    ModelEgressPolicyProvider,
    ModelProfileProvider,
    ModelProvider,
    StructuredOutputValidator,
)
from pangi.application.services.redaction import RedactionService
from pangi.domain.model_routing import (
    DataClass,
    ModelFinishReason,
    ModelPolicyErrorCode,
    ModelPolicyOutcome,
    ModelPolicyStage,
    ModelProviderErrorCode,
    ModelRetention,
    data_class_rank,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _identifier() -> str:
    return uuid.uuid4().hex


def _classification(request: ModelCallRequest) -> tuple[tuple[DataClass, ...], DataClass]:
    values = {data_class for source in request.sources for data_class in source.data_classes}
    ordered = tuple(sorted(values, key=data_class_rank))
    return ordered, ordered[-1]


def _source_kinds(request: ModelCallRequest) -> tuple[str, ...]:
    return tuple(sorted({source.source_kind for source in request.sources}))


def _region_allowed(profile: ModelProfile, policy: ModelEgressPolicy) -> bool:
    if profile.region is None:
        return not policy.allowed_regions
    return profile.region in policy.allowed_regions


def _is_candidate_allowed(
    candidate: ModelProfile,
    *,
    request: ModelCallRequest,
    policy: ModelEgressPolicy,
    data_classes: frozenset[DataClass],
    source_kinds: frozenset[str],
) -> bool:
    contains_raw_content = any(source.raw_content for source in request.sources)
    return (
        candidate.active
        and candidate.profile == request.profile
        and candidate.provider in policy.allowed_providers
        and candidate.model in policy.allowed_models
        and _region_allowed(candidate, policy)
        and request.purpose in policy.allowed_purposes
        and request.purpose in candidate.supported_purposes
        and data_classes.issubset(policy.allowed_data_classes)
        and data_classes.issubset(candidate.supported_data_classes)
        and source_kinds.issubset(policy.allowed_source_kinds)
        and source_kinds.issubset(candidate.supported_source_kinds)
        and (
            not policy.require_zero_retention
            or candidate.retention is ModelRetention.ZERO_RETENTION
        )
        and (
            not contains_raw_content
            or (policy.allow_raw_content and candidate.allow_raw_content)
        )
    )


class ModelPolicyService:
    """Resolve one explicit Profile and fail closed before Provider execution."""

    def __init__(
        self,
        *,
        profiles: ModelProfileProvider,
        policies: ModelEgressPolicyProvider,
        redactor: RedactionService,
    ) -> None:
        self._profiles = profiles
        self._policies = policies
        self._redactor = redactor

    async def guard(self, request: ModelCallRequest) -> GuardedModelRequest:
        data_classes, highest_data_class = _classification(request)
        source_kinds = _source_kinds(request)
        policy = await self._policies.get_policy(request.profile)
        if policy is None or policy.profile != request.profile:
            self._block(
                request,
                data_classes=data_classes,
                highest_data_class=highest_data_class,
                source_kinds=source_kinds,
                stage=ModelPolicyStage.POLICY,
                error_code=ModelPolicyErrorCode.POLICY_MISSING,
            )

        candidates = await self._profiles.list_candidates(request.profile)
        if not isinstance(candidates, tuple) or any(
            not isinstance(candidate, ModelProfile) for candidate in candidates
        ):
            self._block(
                request,
                data_classes=data_classes,
                highest_data_class=highest_data_class,
                source_kinds=source_kinds,
                stage=ModelPolicyStage.CANDIDATE,
                error_code=ModelPolicyErrorCode.POLICY_DENIED,
                policy=policy,
            )
        identifiers = tuple(candidate.profile_id for candidate in candidates)
        priorities = tuple(candidate.routing_priority for candidate in candidates)
        if len(identifiers) != len(set(identifiers)) or len(priorities) != len(set(priorities)):
            self._block(
                request,
                data_classes=data_classes,
                highest_data_class=highest_data_class,
                source_kinds=source_kinds,
                stage=ModelPolicyStage.CANDIDATE,
                error_code=ModelPolicyErrorCode.POLICY_DENIED,
                policy=policy,
                evaluated_candidate_count=len(candidates),
            )

        requested_classes = frozenset(data_classes)
        requested_source_kinds = frozenset(source_kinds)
        eligible = tuple(
            sorted(
                (
                    candidate
                    for candidate in candidates
                    if _is_candidate_allowed(
                        candidate,
                        request=request,
                        policy=policy,
                        data_classes=requested_classes,
                        source_kinds=requested_source_kinds,
                    )
                ),
                key=lambda candidate: candidate.routing_priority,
            )
        )
        if not eligible:
            self._block(
                request,
                data_classes=data_classes,
                highest_data_class=highest_data_class,
                source_kinds=source_kinds,
                stage=ModelPolicyStage.CANDIDATE,
                error_code=ModelPolicyErrorCode.POLICY_DENIED,
                policy=policy,
                evaluated_candidate_count=len(candidates),
            )
        selected = eligible[0]

        try:
            safe_sources, redaction = self._redact_sources(request.sources)
        except (RedactionInputError, RuntimeError, TypeError, ValueError):
            self._block(
                request,
                data_classes=data_classes,
                highest_data_class=highest_data_class,
                source_kinds=source_kinds,
                stage=ModelPolicyStage.REDACTION,
                error_code=ModelPolicyErrorCode.REDACTION_FAILED,
                policy=policy,
                evaluated_candidate_count=len(candidates),
                eligible_candidate_count=len(eligible),
                selected=selected,
            )
        input_fingerprint = self._input_fingerprint(
            request=request,
            safe_sources=safe_sources,
            policy=policy,
            selected=selected,
            redaction=redaction,
        )
        decision = self._decision(
            request,
            data_classes=data_classes,
            highest_data_class=highest_data_class,
            source_kinds=source_kinds,
            stage=ModelPolicyStage.COMPLETE,
            outcome=ModelPolicyOutcome.ALLOWED,
            policy=policy,
            evaluated_candidate_count=len(candidates),
            eligible_candidate_count=len(eligible),
            selected=selected,
            redaction=redaction,
            input_fingerprint=input_fingerprint,
        )
        return GuardedModelRequest(
            logical_call_id=request.logical_call_id,
            profile=selected,
            purpose=request.purpose,
            sources=safe_sources,
            output_schema=request.output_schema,
            input_fingerprint=input_fingerprint,
            decision=decision,
        )

    def _redact_sources(
        self,
        sources: tuple[ModelInputSource, ...],
    ) -> tuple[tuple[ModelInputSource, ...], RedactionSummary]:
        safe_sources: list[ModelInputSource] = []
        summaries: list[RedactionSummary] = []
        for source in sources:
            result = self._redactor.redact_text(source.content)
            if not isinstance(result.value, str):
                raise TypeError("Model text redaction returned an invalid result")
            safe_data_json: str | None = None
            data_summary: RedactionSummary | None = None
            if source.canonical_data_json is not None:
                source_data = json.loads(source.canonical_data_json)
                data_result = self._redactor.redact_data(source_data)
                if not isinstance(data_result.value, dict):
                    raise TypeError("Model structured redaction returned an invalid result")
                safe_data_json = json.dumps(
                    data_result.value,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                data_summary = data_result.summary
            safe_sources.append(
                ModelInputSource(
                    source_kind=source.source_kind,
                    data_classes=source.data_classes,
                    content=result.value,
                    raw_content=source.raw_content,
                    role=source.role,
                    canonical_data_json=safe_data_json,
                )
            )
            summaries.append(result.summary)
            if data_summary is not None:
                summaries.append(data_summary)
        first = summaries[0]
        if any(
            summary.policy_version != first.policy_version
            or summary.policy_fingerprint != first.policy_fingerprint
            for summary in summaries[1:]
        ):
            raise RuntimeError("Model redaction policy changed during one logical call")
        applied_rule_ids = tuple(
            dict.fromkeys(
                rule_id for summary in summaries for rule_id in summary.applied_rule_ids
            )
        )
        return tuple(safe_sources), RedactionSummary(
            policy_version=first.policy_version,
            policy_fingerprint=first.policy_fingerprint,
            redaction_count=sum(summary.redaction_count for summary in summaries),
            applied_rule_ids=applied_rule_ids,
        )

    @staticmethod
    def _input_fingerprint(
        *,
        request: ModelCallRequest,
        safe_sources: tuple[ModelInputSource, ...],
        policy: ModelEgressPolicy,
        selected: ModelProfile,
        redaction: RedactionSummary,
    ) -> str:
        payload = {
            "output_schema_fingerprint": request.output_schema.fingerprint,
            "policy_fingerprint": policy.fingerprint,
            "profile_fingerprint": selected.fingerprint,
            "purpose": request.purpose.value,
            "redaction_policy_fingerprint": redaction.policy_fingerprint,
            "sources": [
                {
                    "content": source.content,
                    "data": source.canonical_data_json,
                    "data_classes": sorted(value.value for value in source.data_classes),
                    "raw_content": source.raw_content,
                    "role": source.role.value,
                    "source_kind": source.source_kind,
                }
                for source in safe_sources
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _decision(
        request: ModelCallRequest,
        *,
        data_classes: tuple[DataClass, ...],
        highest_data_class: DataClass,
        source_kinds: tuple[str, ...],
        stage: ModelPolicyStage,
        outcome: ModelPolicyOutcome,
        policy: ModelEgressPolicy | None = None,
        evaluated_candidate_count: int = 0,
        eligible_candidate_count: int = 0,
        selected: ModelProfile | None = None,
        redaction: RedactionSummary | None = None,
        input_fingerprint: str | None = None,
        error_code: ModelPolicyErrorCode | None = None,
    ) -> ModelPolicyDecision:
        return ModelPolicyDecision(
            profile=request.profile,
            purpose=request.purpose,
            stage=stage,
            outcome=outcome,
            data_classes=data_classes,
            highest_data_class=highest_data_class,
            source_kinds=source_kinds,
            evaluated_candidate_count=evaluated_candidate_count,
            eligible_candidate_count=eligible_candidate_count,
            policy_id=policy.policy_id if policy is not None else None,
            policy_version=policy.policy_version if policy is not None else None,
            policy_fingerprint=policy.fingerprint if policy is not None else None,
            selected_profile_id=selected.profile_id if selected is not None else None,
            selected_profile_fingerprint=(
                selected.fingerprint if selected is not None else None
            ),
            provider=selected.provider if selected is not None else None,
            model=selected.model if selected is not None else None,
            region=selected.region if selected is not None else None,
            redaction=redaction,
            input_fingerprint=input_fingerprint,
            error_code=error_code,
        )

    def _block(
        self,
        request: ModelCallRequest,
        *,
        data_classes: tuple[DataClass, ...],
        highest_data_class: DataClass,
        source_kinds: tuple[str, ...],
        stage: ModelPolicyStage,
        error_code: ModelPolicyErrorCode,
        policy: ModelEgressPolicy | None = None,
        evaluated_candidate_count: int = 0,
        eligible_candidate_count: int = 0,
        selected: ModelProfile | None = None,
    ) -> NoReturn:
        raise ModelPolicyBlockedError(
            self._decision(
                request,
                data_classes=data_classes,
                highest_data_class=highest_data_class,
                source_kinds=source_kinds,
                stage=stage,
                outcome=ModelPolicyOutcome.BLOCKED,
                policy=policy,
                evaluated_candidate_count=evaluated_candidate_count,
                eligible_candidate_count=eligible_candidate_count,
                selected=selected,
                error_code=error_code,
            )
        )


class GuardedModelExecutionService:
    """Make Model policy, redaction, and output validation mandatory."""

    def __init__(
        self,
        policy: ModelPolicyService,
        *,
        provider: ModelProvider,
        output_validator: StructuredOutputValidator,
        invocations: ModelInvocationRecorder,
        clock: Clock = _utc_now,
        id_factory: IdFactory = _identifier,
    ) -> None:
        self._policy = policy
        self._provider = provider
        self._output_validator = output_validator
        self._invocations = invocations
        self._clock = clock
        self._id_factory = id_factory

    async def execute(
        self,
        request: ModelCallRequest,
        *,
        context: ModelInvocationContext,
    ) -> GuardedModelExecution:
        invocation_id = self._id_factory()
        call_fingerprint = logical_call_fingerprint(request.logical_call_id)
        try:
            guarded = await self._policy.guard(request)
        except ModelPolicyBlockedError as blocked:
            await self._invocations.deny(
                ModelInvocationDenial(
                    invocation_id=invocation_id,
                    context=context,
                    logical_call_fingerprint=call_fingerprint,
                    decision=blocked.decision,
                    denied_at=self._clock(),
                )
            )
            raise
        await self._invocations.start(
            ModelInvocationStart(
                invocation_id=invocation_id,
                context=context,
                logical_call_fingerprint=call_fingerprint,
                decision=guarded.decision,
                started_at=self._clock(),
            )
        )
        try:
            response = await self._invoke(guarded)
        except ModelProviderFailure as provider_failure:
            await self._invocations.finish(
                ModelInvocationFinish.failed(
                    invocation_id,
                    provider_failure,
                    finished_at=self._clock(),
                )
            )
            raise
        except Exception:
            unknown_failure = ModelProviderFailure(
                ModelProviderErrorCode.UNKNOWN,
                retryable=False,
            )
            await self._invocations.finish(
                ModelInvocationFinish.failed(
                    invocation_id,
                    unknown_failure,
                    finished_at=self._clock(),
                )
            )
            raise unknown_failure from None
        await self._invocations.finish(
            ModelInvocationFinish.completed(
                invocation_id,
                response,
                finished_at=self._clock(),
            )
        )
        return GuardedModelExecution(response=response, decision=guarded.decision)

    async def _invoke(self, guarded: GuardedModelRequest) -> ModelProviderResponse:
        response = await self._provider.invoke(guarded)
        if not isinstance(response, ModelProviderResponse):
            raise ModelProviderFailure(ModelProviderErrorCode.UNKNOWN, retryable=False)
        if response.finish_reason is not ModelFinishReason.STOP:
            code = (
                ModelProviderErrorCode.CONTENT_FILTERED
                if response.finish_reason is ModelFinishReason.CONTENT_FILTERED
                else ModelProviderErrorCode.INVALID_STRUCTURED_OUTPUT
            )
            raise ModelProviderFailure(
                code,
                retryable=False,
                provider_request_count=response.provider_request_count,
                duration_ms=response.duration_ms,
                token_usage=response.token_usage,
                provider_latency_ms=response.provider_latency_ms,
                finish_reason=response.finish_reason,
                output_fingerprint=response.output_fingerprint,
            )
        try:
            valid = self._output_validator.is_valid(
                schema=guarded.output_schema,
                canonical_output_json=response.canonical_output_json,
            )
        except Exception:
            valid = False
        if not valid:
            raise ModelProviderFailure(
                ModelProviderErrorCode.INVALID_STRUCTURED_OUTPUT,
                retryable=False,
                provider_request_count=response.provider_request_count,
                duration_ms=response.duration_ms,
                token_usage=response.token_usage,
                provider_latency_ms=response.provider_latency_ms,
                finish_reason=response.finish_reason,
                output_fingerprint=response.output_fingerprint,
            )
        return response
