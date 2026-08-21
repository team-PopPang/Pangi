"""Policy-selected Provider Router tests without external SDKs or Network calls."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from pangi.adapters.outbound.model_providers.common import (
    OptionalModelProviderDependencyError,
)
from pangi.adapters.outbound.model_providers.router import PolicySelectedModelProvider
from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelInputSource,
    ModelPolicyDecision,
    ModelProfile,
    ModelProviderFailure,
    ModelProviderResponse,
    ProviderRetryPolicy,
    StructuredOutputSchema,
)
from pangi.application.contracts.redaction import RedactionSummary
from pangi.domain.model_routing import (
    DataClass,
    ModelMessageRole,
    ModelPolicyOutcome,
    ModelPolicyStage,
    ModelProviderErrorCode,
    ModelPurpose,
    ModelRetention,
)


def _retry_policy() -> ProviderRetryPolicy:
    return ProviderRetryPolicy(
        max_attempts=3,
        attempt_timeout_seconds=20,
        total_timeout_seconds=60,
        retry_backoff_seconds=(0.1, 0.5),
    )


def _guarded(*, provider: str, region: str | None = None) -> GuardedModelRequest:
    profile = ModelProfile(
        profile_id=f"root-{provider}",
        profile="root-default",
        profile_version="profile-v1",
        provider=provider,
        model="test-model",
        region=region,
        supported_data_classes=frozenset({DataClass.INTERNAL}),
        supported_source_kinds=frozenset({"channel", "policy"}),
        supported_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        retention=ModelRetention.ZERO_RETENTION,
        allow_raw_content=True,
        routing_priority=1,
    )
    input_fingerprint = "a" * 64
    decision = ModelPolicyDecision(
        profile="root-default",
        purpose=ModelPurpose.ORCHESTRATION,
        stage=ModelPolicyStage.COMPLETE,
        outcome=ModelPolicyOutcome.ALLOWED,
        data_classes=(DataClass.INTERNAL,),
        highest_data_class=DataClass.INTERNAL,
        source_kinds=("channel", "policy"),
        evaluated_candidate_count=1,
        eligible_candidate_count=1,
        policy_id="root-egress",
        policy_version="policy-v1",
        policy_fingerprint="b" * 64,
        selected_profile_id=profile.profile_id,
        selected_profile_fingerprint=profile.fingerprint,
        provider=provider,
        model=profile.model,
        region=region,
        redaction=RedactionSummary(
            policy_version="redaction-v1",
            policy_fingerprint="c" * 64,
            redaction_count=0,
            applied_rule_ids=(),
        ),
        input_fingerprint=input_fingerprint,
    )
    return GuardedModelRequest(
        logical_call_id="logical-provider-call",
        profile=profile,
        purpose=ModelPurpose.ORCHESTRATION,
        sources=(
            ModelInputSource(
                source_kind="policy",
                data_classes=frozenset({DataClass.INTERNAL}),
                content="Return JSON.",
                raw_content=False,
                role=ModelMessageRole.SYSTEM,
            ),
            ModelInputSource(
                source_kind="channel",
                data_classes=frozenset({DataClass.INTERNAL}),
                content="Summarize the request.",
                raw_content=False,
                role=ModelMessageRole.USER,
            ),
        ),
        output_schema=StructuredOutputSchema(
            name="result-v1",
            canonical_schema_json='{"type":"object"}',
        ),
        input_fingerprint=input_fingerprint,
        decision=decision,
    )


class RecordingProvider:
    def __init__(self, failure: ModelProviderFailure | None = None) -> None:
        self.calls = 0
        self.failure = failure

    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        del request
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return ModelProviderResponse('{"answer":"ok"}')


def test_router_lazily_builds_and_caches_only_selected_provider() -> None:
    openai = RecordingProvider()
    bedrock = RecordingProvider()
    factory_calls: list[tuple[str, str | None]] = []

    def openai_factory(profile: ModelProfile, policy: ProviderRetryPolicy) -> RecordingProvider:
        assert policy == _retry_policy()
        factory_calls.append(("openai", profile.region))
        return openai

    def bedrock_factory(profile: ModelProfile, policy: ProviderRetryPolicy) -> RecordingProvider:
        del policy
        factory_calls.append(("bedrock", profile.region))
        return bedrock

    router = PolicySelectedModelProvider(
        _retry_policy(),
        factories={"bedrock": bedrock_factory, "openai": openai_factory},
    )

    assert factory_calls == []
    asyncio.run(router.invoke(_guarded(provider="openai")))
    asyncio.run(router.invoke(_guarded(provider="openai")))

    assert factory_calls == [("openai", None)]
    assert openai.calls == 2
    assert bedrock.calls == 0


def test_router_uses_selected_bedrock_region_without_fallback() -> None:
    bedrock = RecordingProvider(
        ModelProviderFailure(ModelProviderErrorCode.UNAVAILABLE, retryable=False)
    )
    fallback_calls = 0

    def bedrock_factory(profile: ModelProfile, policy: ProviderRetryPolicy) -> RecordingProvider:
        del policy
        assert profile.region == "us-east-1"
        return bedrock

    def openai_factory(profile: ModelProfile, policy: ProviderRetryPolicy) -> RecordingProvider:
        nonlocal fallback_calls
        del profile, policy
        fallback_calls += 1
        return RecordingProvider()

    router = PolicySelectedModelProvider(
        _retry_policy(),
        factories={"bedrock": bedrock_factory, "openai": openai_factory},
    )

    with pytest.raises(ModelProviderFailure) as captured:
        asyncio.run(router.invoke(_guarded(provider="bedrock", region="us-east-1")))

    assert captured.value.code is ModelProviderErrorCode.UNAVAILABLE
    assert bedrock.calls == 1
    assert fallback_calls == 0


@pytest.mark.parametrize(
    ("provider", "factories", "expected_code"),
    [
        ("unsupported", {"openai": lambda profile, policy: RecordingProvider()}, "invalid"),
        (
            "openai",
            {
                "openai": lambda profile, policy: (_ for _ in ()).throw(
                    OptionalModelProviderDependencyError("openai")
                )
            },
            "unavailable",
        ),
    ],
)
def test_router_normalizes_preflight_failures_without_private_details(
    provider: str,
    factories: dict[str, object],
    expected_code: str,
) -> None:
    router = PolicySelectedModelProvider(  # type: ignore[arg-type]
        _retry_policy(),
        factories=factories,
    )

    with pytest.raises(ModelProviderFailure) as captured:
        asyncio.run(router.invoke(_guarded(provider=provider)))

    code = captured.value.code
    if expected_code == "invalid":
        assert code is ModelProviderErrorCode.INVALID_REQUEST
    else:
        assert code is ModelProviderErrorCode.UNAVAILABLE
    assert "private" not in str(captured.value)


def test_default_bedrock_factory_rejects_missing_region_before_sdk_import() -> None:
    router = PolicySelectedModelProvider(_retry_policy())

    with pytest.raises(ModelProviderFailure) as captured:
        asyncio.run(router.invoke(_guarded(provider="bedrock")))

    assert captured.value.code is ModelProviderErrorCode.INVALID_REQUEST


def test_router_rejects_profile_metadata_changed_after_policy_selection() -> None:
    factory_calls = 0

    def factory(profile: ModelProfile, policy: ProviderRetryPolicy) -> RecordingProvider:
        nonlocal factory_calls
        del profile, policy
        factory_calls += 1
        return RecordingProvider()

    router = PolicySelectedModelProvider(
        _retry_policy(),
        factories={"bedrock": factory, "openai": factory},
    )
    admitted = _guarded(provider="openai")
    changed_profile = replace(
        admitted.profile,
        provider="bedrock",
        region="us-east-1",
    )
    changed_request = replace(admitted, profile=changed_profile)

    with pytest.raises(ModelProviderFailure) as captured:
        asyncio.run(router.invoke(changed_request))

    assert captured.value.code is ModelProviderErrorCode.INVALID_REQUEST
    assert factory_calls == 0
