"""Logical Model call, Network Retry, and structured output contract."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from pangi.adapters.outbound.model_providers.json_schema import JsonSchemaOutputValidator
from pangi.adapters.outbound.model_providers.retry import RetryingModelProvider
from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelCallRequest,
    ModelEgressPolicy,
    ModelInputSource,
    ModelProfile,
    ModelProviderFailure,
    ModelProviderResponse,
    ProviderRetryPolicy,
    StructuredOutputSchema,
)
from pangi.application.services.model_routing import (
    GuardedModelExecutionService,
    ModelPolicyService,
)
from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)
from pangi.domain.model_routing import (
    DataClass,
    ModelProviderErrorCode,
    ModelPurpose,
    ModelRetention,
)


class ContractProfiles:
    async def list_candidates(self, profile: str) -> tuple[ModelProfile, ...]:
        return (
            ModelProfile(
                profile_id="contract-openai",
                profile=profile,
                profile_version="profile-v1",
                provider="openai",
                model="contract-model",
                region=None,
                supported_data_classes=frozenset({DataClass.INTERNAL}),
                supported_source_kinds=frozenset({"channel"}),
                supported_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
                retention=ModelRetention.PROVIDER_DEFAULT,
                allow_raw_content=False,
                routing_priority=1,
            ),
        )


class ContractPolicies:
    async def get_policy(self, profile: str) -> ModelEgressPolicy | None:
        return ModelEgressPolicy(
            policy_id="contract-egress",
            policy_version="policy-v1",
            profile=profile,
            allowed_providers=frozenset({"openai"}),
            allowed_models=frozenset({"contract-model"}),
            allowed_regions=frozenset(),
            allowed_data_classes=frozenset({DataClass.INTERNAL}),
            allowed_source_kinds=frozenset({"channel"}),
            allowed_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
            require_redaction=True,
            require_zero_retention=False,
            allow_raw_content=False,
        )


def _request() -> ModelCallRequest:
    return ModelCallRequest(
        logical_call_id="one-logical-call",
        profile="contract-profile",
        purpose=ModelPurpose.ORCHESTRATION,
        sources=(
            ModelInputSource(
                source_kind="channel",
                data_classes=frozenset({DataClass.INTERNAL}),
                content="safe request",
                raw_content=False,
            ),
        ),
        output_schema=StructuredOutputSchema(
            name="contract-result",
            canonical_schema_json=(
                '{"additionalProperties":false,"properties":{"answer":{"type":"string"}},'
                '"required":["answer"],"type":"object"}'
            ),
        ),
    )


def _policy_service() -> ModelPolicyService:
    return ModelPolicyService(
        profiles=ContractProfiles(),
        policies=ContractPolicies(),
        redactor=RedactionService(core_secret_redaction_policy()),
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


class AdvancingSleeper:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.delays: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.clock.now += seconds


class FlakyProvider:
    def __init__(self, outcomes: tuple[object, ...]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[GuardedModelRequest] = []

    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        self.calls.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, ModelProviderResponse)
        return outcome


def _execution(
    provider: FlakyProvider,
    *,
    clock: Callable[[], float],
    sleeper: AdvancingSleeper,
) -> GuardedModelExecutionService:
    retrying = RetryingModelProvider(
        provider,
        ProviderRetryPolicy(
            max_attempts=3,
            attempt_timeout_seconds=5,
            total_timeout_seconds=20,
            retry_backoff_seconds=(0.1, 0.2),
        ),
        clock=clock,
        sleeper=sleeper,
    )
    return GuardedModelExecutionService(
        _policy_service(),
        provider=retrying,
        output_validator=JsonSchemaOutputValidator(),
    )


def test_transport_retry_counts_requests_under_one_logical_call() -> None:
    transient = ModelProviderFailure(ModelProviderErrorCode.UNAVAILABLE, retryable=True)
    provider = FlakyProvider(
        (transient, transient, ModelProviderResponse('{"answer":"safe"}'))
    )
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)

    result = asyncio.run(_execution(provider, clock=clock, sleeper=sleeper).execute(_request()))

    assert len(provider.calls) == 3
    assert {call.logical_call_id for call in provider.calls} == {"one-logical-call"}
    assert result.response.provider_request_count == 3
    assert result.response.duration_ms == 300
    assert sleeper.delays == [0.1, 0.2]


def test_non_retryable_failure_stops_after_one_network_request() -> None:
    provider = FlakyProvider(
        (
            ModelProviderFailure(
                ModelProviderErrorCode.INVALID_REQUEST,
                retryable=False,
            ),
        )
    )
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)

    with pytest.raises(ModelProviderFailure) as captured:
        asyncio.run(_execution(provider, clock=clock, sleeper=sleeper).execute(_request()))

    assert captured.value.code is ModelProviderErrorCode.INVALID_REQUEST
    assert captured.value.provider_request_count == 1
    assert len(provider.calls) == 1
    assert sleeper.delays == []


def test_retry_exhaustion_reports_all_network_requests() -> None:
    transient = ModelProviderFailure(ModelProviderErrorCode.TIMEOUT, retryable=True)
    provider = FlakyProvider((transient, transient, transient))
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)

    with pytest.raises(ModelProviderFailure) as captured:
        asyncio.run(_execution(provider, clock=clock, sleeper=sleeper).execute(_request()))

    assert captured.value.code is ModelProviderErrorCode.TIMEOUT
    assert not captured.value.retryable
    assert captured.value.provider_request_count == 3
    assert len(provider.calls) == 3
    assert sleeper.delays == [0.1, 0.2]


def test_invalid_structured_output_fails_without_semantic_retry() -> None:
    provider = FlakyProvider((ModelProviderResponse('{"answer":42}'),))
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)

    with pytest.raises(ModelProviderFailure) as captured:
        asyncio.run(_execution(provider, clock=clock, sleeper=sleeper).execute(_request()))

    assert captured.value.code is ModelProviderErrorCode.INVALID_STRUCTURED_OUTPUT
    assert not captured.value.retryable
    assert captured.value.provider_request_count == 1
    assert len(provider.calls) == 1
    assert sleeper.delays == []
