"""Mandatory Model Egress boundary contract across policy, redaction, and Provider."""

from __future__ import annotations

import asyncio

import pytest

from pangi.adapters.outbound.model_providers.json_schema import JsonSchemaOutputValidator
from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelCallRequest,
    ModelEgressPolicy,
    ModelInputSource,
    ModelPolicyBlockedError,
    ModelProfile,
    ModelProviderResponse,
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
from pangi.domain.model_routing import DataClass, ModelPurpose, ModelRetention


class ContractProfiles:
    def __init__(self, profile: ModelProfile) -> None:
        self.profile = profile

    async def list_candidates(self, profile: str) -> tuple[ModelProfile, ...]:
        return (self.profile,)


class ContractPolicies:
    def __init__(self, policy: ModelEgressPolicy) -> None:
        self.policy = policy

    async def get_policy(self, profile: str) -> ModelEgressPolicy | None:
        return self.policy


class ContractProvider:
    def __init__(self) -> None:
        self.calls: list[GuardedModelRequest] = []

    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        self.calls.append(request)
        return ModelProviderResponse('{"answer":"safe"}')


def _profile() -> ModelProfile:
    return ModelProfile(
        profile_id="contract-model",
        profile="contract-profile",
        profile_version="profile-v1",
        provider="openai",
        model="contract-model-v1",
        region="us-east-1",
        supported_data_classes=frozenset({DataClass.PUBLIC, DataClass.INTERNAL}),
        supported_source_kinds=frozenset({"channel"}),
        supported_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        retention=ModelRetention.ZERO_RETENTION,
        allow_raw_content=False,
        routing_priority=1,
    )


def _policy() -> ModelEgressPolicy:
    return ModelEgressPolicy(
        policy_id="contract-egress",
        policy_version="policy-v1",
        profile="contract-profile",
        allowed_providers=frozenset({"openai"}),
        allowed_models=frozenset({"contract-model-v1"}),
        allowed_regions=frozenset({"us-east-1"}),
        allowed_data_classes=frozenset({DataClass.PUBLIC, DataClass.INTERNAL}),
        allowed_source_kinds=frozenset({"channel"}),
        allowed_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        require_redaction=True,
        require_zero_retention=True,
        allow_raw_content=False,
    )


def _request(data_class: DataClass, content: str) -> ModelCallRequest:
    return ModelCallRequest(
        logical_call_id="contract-logical-call",
        profile="contract-profile",
        purpose=ModelPurpose.ORCHESTRATION,
        sources=(
            ModelInputSource(
                source_kind="channel",
                data_classes=frozenset({data_class}),
                content=content,
                raw_content=False,
                canonical_data_json='{"password":"contract-field-secret"}',
            ),
        ),
        output_schema=StructuredOutputSchema(
            name="contract-output-v1",
            canonical_schema_json='{"type":"object"}',
        ),
    )


def test_denied_data_never_calls_provider_and_allowed_data_is_redacted_first() -> None:
    provider = ContractProvider()
    policy_service = ModelPolicyService(
        profiles=ContractProfiles(_profile()),
        policies=ContractPolicies(_policy()),
        redactor=RedactionService(core_secret_redaction_policy()),
    )
    execution = GuardedModelExecutionService(
        policy_service,
        provider=provider,
        output_validator=JsonSchemaOutputValidator(),
    )

    with pytest.raises(ModelPolicyBlockedError):
        asyncio.run(execution.execute(_request(DataClass.RESTRICTED, "restricted")))
    assert provider.calls == []

    secret = "sk-contract-secret-12345"
    asyncio.run(execution.execute(_request(DataClass.INTERNAL, secret)))

    assert len(provider.calls) == 1
    assert secret not in provider.calls[0].sources[0].content
    assert provider.calls[0].sources[0].content == "[REDACTED]"
    assert provider.calls[0].sources[0].canonical_data_json == (
        '{"password":"[REDACTED]"}'
    )
