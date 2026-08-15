"""Provider-specific request mapping and safe response normalization."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from pangi.adapters.outbound.model_providers import bedrock as bedrock_adapter
from pangi.adapters.outbound.model_providers import openai as openai_adapter
from pangi.adapters.outbound.model_providers.bedrock import BedrockModelProvider
from pangi.adapters.outbound.model_providers.common import (
    OptionalModelProviderDependencyError,
)
from pangi.adapters.outbound.model_providers.openai import OpenAIModelProvider
from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelInputSource,
    ModelPolicyDecision,
    ModelProfile,
    ModelProviderFailure,
    ProviderRetryPolicy,
    StructuredOutputSchema,
)
from pangi.application.contracts.redaction import RedactionSummary
from pangi.domain.model_routing import (
    DataClass,
    ModelFinishReason,
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


def _guarded(*, provider: str, model: str, region: str | None) -> GuardedModelRequest:
    profile = ModelProfile(
        profile_id=f"root-{provider}",
        profile="root-default",
        profile_version="profile-v1",
        provider=provider,
        model=model,
        region=region,
        supported_data_classes=frozenset({DataClass.INTERNAL}),
        supported_source_kinds=frozenset({"channel", "policy"}),
        supported_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        retention=ModelRetention.PROVIDER_DEFAULT,
        allow_raw_content=False,
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
        policy_version="policy-v1",
        policy_fingerprint="b" * 64,
        selected_profile_id=profile.profile_id,
        selected_profile_fingerprint=profile.fingerprint,
        provider=provider,
        model=model,
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
                content="Return the requested JSON object.",
                raw_content=False,
                role=ModelMessageRole.SYSTEM,
            ),
            ModelInputSource(
                source_kind="channel",
                data_classes=frozenset({DataClass.INTERNAL}),
                content="Summarize the safe request.",
                raw_content=False,
                role=ModelMessageRole.USER,
                canonical_data_json='{"safe":"value"}',
            ),
        ),
        output_schema=StructuredOutputSchema(
            name="agent-result-v1",
            canonical_schema_json=(
                '{"additionalProperties":false,"properties":{"answer":{"type":"string"}},'
                '"required":["answer"],"type":"object"}'
            ),
        ),
        input_fingerprint=input_fingerprint,
        decision=decision,
    )


class FakeOpenAIResponses:
    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        return self.response


class FakeOpenAIClient:
    def __init__(self, response: object) -> None:
        self.responses = FakeOpenAIResponses(response)


def test_openai_adapter_uses_responses_strict_schema_and_normalizes_usage() -> None:
    client = FakeOpenAIClient(
        SimpleNamespace(
            output_text='{"answer":"safe"}',
            status="completed",
            usage=SimpleNamespace(input_tokens=12, output_tokens=4, total_tokens=16),
        )
    )
    adapter = OpenAIModelProvider(client)

    response = asyncio.run(
        adapter.invoke(_guarded(provider="openai", model="gpt-example", region=None))
    )

    request = client.responses.requests[0]
    assert request["model"] == "gpt-example"
    assert request["store"] is False
    assert request["text"] == {
        "format": {
            "name": "agent-result-v1",
            "schema": {
                "additionalProperties": False,
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "type": "object",
            },
            "strict": True,
            "type": "json_schema",
        }
    }
    assert '"source_kind":"policy"' in str(request["instructions"])
    assert '"source_kind":"channel"' in str(request["input"])
    assert response.token_usage is not None
    assert response.token_usage.total_tokens == 16
    assert response.finish_reason is ModelFinishReason.STOP


class FakeBedrockClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.thread_ids: list[int] = []

    def converse(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        self.thread_ids.append(threading.get_ident())
        return {
            "metrics": {"latencyMs": 321},
            "output": {"message": {"content": [{"text": '{"answer":"safe"}'}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 9, "outputTokens": 3, "totalTokens": 12},
        }


def test_bedrock_adapter_uses_converse_schema_off_the_event_loop() -> None:
    client = FakeBedrockClient()
    adapter = BedrockModelProvider(client)
    caller_thread = threading.get_ident()

    response = asyncio.run(
        adapter.invoke(
            _guarded(
                provider="bedrock",
                model="anthropic.example-v1:0",
                region="us-east-1",
            )
        )
    )

    request = client.requests[0]
    output_config = request["outputConfig"]
    assert isinstance(output_config, dict)
    json_schema = output_config["textFormat"]["structure"]["jsonSchema"]
    assert json_schema["name"] == "agent-result-v1"
    assert isinstance(json_schema["schema"], str)
    assert request["modelId"] == "anthropic.example-v1:0"
    assert '"source_kind":"policy"' in str(request["system"])
    assert len(client.thread_ids) == 1
    assert client.thread_ids[0] != caller_thread
    assert response.provider_latency_ms == 321
    assert response.token_usage is not None
    assert response.token_usage.total_tokens == 12


class RateLimitError(RuntimeError):
    status_code = 429


class FailingOpenAIResponses:
    async def create(self, **kwargs: object) -> object:
        raise RateLimitError("private Provider error")


class FailingOpenAIClient:
    responses = FailingOpenAIResponses()


def test_provider_errors_are_normalized_without_raw_messages() -> None:
    adapter = OpenAIModelProvider(FailingOpenAIClient())

    with pytest.raises(ModelProviderFailure) as captured:
        asyncio.run(adapter.invoke(_guarded(provider="openai", model="gpt-example", region=None)))

    assert captured.value.code is ModelProviderErrorCode.RATE_LIMITED
    assert captured.value.retryable
    assert "private Provider error" not in str(captured.value)
    assert "private Provider error" not in repr(captured.value)


def test_provider_content_filter_is_not_retried_as_transport_failure() -> None:
    client = FakeOpenAIClient(
        SimpleNamespace(
            incomplete_details=SimpleNamespace(reason="content_filter"),
            output_text="",
            status="incomplete",
            usage=None,
        )
    )

    with pytest.raises(ModelProviderFailure) as captured:
        asyncio.run(
            OpenAIModelProvider(client).invoke(
                _guarded(provider="openai", model="gpt-example", region=None)
            )
        )

    assert captured.value.code is ModelProviderErrorCode.CONTENT_FILTERED
    assert not captured.value.retryable


def test_openai_factory_disables_sdk_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_client(**kwargs: object) -> FakeOpenAIClient:
        captured.update(kwargs)
        return FakeOpenAIClient(SimpleNamespace())

    monkeypatch.setattr(
        openai_adapter.importlib,
        "import_module",
        lambda name: SimpleNamespace(AsyncOpenAI=fake_client),
    )

    openai_adapter.create_openai_provider(retry_policy=_retry_policy())

    assert captured["max_retries"] == 0
    assert captured["timeout"] == 20


def test_bedrock_factory_disables_sdk_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_config: dict[str, object] = {}
    captured_client: dict[str, object] = {}

    def fake_config(**kwargs: object) -> object:
        captured_config.update(kwargs)
        return object()

    def fake_client(service: str, **kwargs: object) -> FakeBedrockClient:
        captured_client["service"] = service
        captured_client.update(kwargs)
        return FakeBedrockClient()

    def fake_import(name: str) -> object:
        if name == "boto3":
            return SimpleNamespace(client=fake_client)
        return SimpleNamespace(Config=fake_config)

    monkeypatch.setattr(bedrock_adapter.importlib, "import_module", fake_import)

    bedrock_adapter.create_bedrock_provider(
        region_name="us-east-1",
        retry_policy=_retry_policy(),
    )

    assert captured_config["retries"] == {"mode": "standard", "total_max_attempts": 1}
    assert captured_config["read_timeout"] == 20
    assert captured_client["service"] == "bedrock-runtime"
    assert captured_client["region_name"] == "us-east-1"


def test_missing_optional_dependency_has_stable_install_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(name: str) -> object:
        raise ModuleNotFoundError("private module path")

    monkeypatch.setattr(openai_adapter.importlib, "import_module", missing)

    with pytest.raises(OptionalModelProviderDependencyError) as captured:
        openai_adapter.create_openai_provider(retry_policy=_retry_policy())

    assert "pangi-agent[openai]" in str(captured.value)
    assert "private module path" not in str(captured.value)
