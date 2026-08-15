"""Deterministic and secret-safe Model routing contracts."""

from __future__ import annotations

import asyncio

import pytest

from pangi.adapters.outbound.model_providers.json_schema import JsonSchemaOutputValidator
from pangi.application.contracts.model_persistence import (
    ModelInvocationContext,
    ModelInvocationDenial,
    ModelInvocationFinish,
    ModelInvocationStart,
)
from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelCallRequest,
    ModelEgressPolicy,
    ModelInputSource,
    ModelPolicyBlockedError,
    ModelProfile,
    ModelProviderFailure,
    ModelProviderResponse,
    StructuredOutputSchema,
)
from pangi.application.contracts.policy_impact import PolicyImpactSnapshot
from pangi.application.ports.model_persistence import ModelInvocationPersistenceError
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
    ModelPolicyErrorCode,
    ModelPolicyOutcome,
    ModelProviderErrorCode,
    ModelPurpose,
    ModelRetention,
)

ALL_DATA_CLASSES = frozenset(DataClass)
ALL_PURPOSES = frozenset(ModelPurpose)


class StubProfileProvider:
    def __init__(self, candidates: tuple[ModelProfile, ...]) -> None:
        self.candidates = candidates
        self.calls: list[str] = []

    async def list_candidates(self, profile: str) -> tuple[ModelProfile, ...]:
        self.calls.append(profile)
        return self.candidates


class StubPolicyProvider:
    def __init__(self, policy: ModelEgressPolicy | None) -> None:
        self.policy = policy
        self.calls: list[str] = []

    async def get_policy(self, profile: str) -> ModelEgressPolicy | None:
        self.calls.append(profile)
        return self.policy


class RecordingModelProvider:
    def __init__(self, output: str = '{"answer":"ok"}') -> None:
        self.output = output
        self.calls: list[GuardedModelRequest] = []

    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        self.calls.append(request)
        return ModelProviderResponse(self.output)


class RecordingInvocationRecorder:
    def __init__(self) -> None:
        self.started: list[ModelInvocationStart] = []
        self.denied: list[ModelInvocationDenial] = []
        self.finished: list[ModelInvocationFinish] = []

    async def start(self, invocation: ModelInvocationStart) -> None:
        self.started.append(invocation)

    async def deny(self, invocation: ModelInvocationDenial) -> None:
        self.denied.append(invocation)

    async def finish(self, invocation: ModelInvocationFinish) -> None:
        self.finished.append(invocation)


class FailingInvocationRecorder(RecordingInvocationRecorder):
    def __init__(self, *, fail_start: bool = False, fail_finish: bool = False) -> None:
        super().__init__()
        self.fail_start = fail_start
        self.fail_finish = fail_finish

    async def start(self, invocation: ModelInvocationStart) -> None:
        await super().start(invocation)
        if self.fail_start:
            raise ModelInvocationPersistenceError("forced start failure")

    async def finish(self, invocation: ModelInvocationFinish) -> None:
        await super().finish(invocation)
        if self.fail_finish:
            raise ModelInvocationPersistenceError("forced finish failure")


CONTEXT = ModelInvocationContext("run-identifier-0001")


def _schema() -> StructuredOutputSchema:
    return StructuredOutputSchema(
        name="agent-result-v1",
        canonical_schema_json='{"required":["answer"],"type":"object"}',
    )


def _source(
    *,
    source_kind: str = "channel",
    data_classes: frozenset[DataClass] = frozenset({DataClass.INTERNAL}),
    content: str = "summarize the request",
    raw_content: bool = False,
    canonical_data_json: str | None = None,
) -> ModelInputSource:
    return ModelInputSource(
        source_kind=source_kind,
        data_classes=data_classes,
        content=content,
        raw_content=raw_content,
        canonical_data_json=canonical_data_json,
    )


def _request(
    *,
    profile: str = "root-default",
    purpose: ModelPurpose = ModelPurpose.ORCHESTRATION,
    sources: tuple[ModelInputSource, ...] | None = None,
) -> ModelCallRequest:
    return ModelCallRequest(
        logical_call_id="logical-call-0001",
        profile=profile,
        purpose=purpose,
        sources=sources or (_source(),),
        output_schema=_schema(),
    )


def _profile(**changes: object) -> ModelProfile:
    values: dict[str, object] = {
        "profile_id": "root-openai-primary",
        "profile": "root-default",
        "profile_version": "profile-v1",
        "provider": "openai",
        "model": "gpt-5.6",
        "region": "us-east-1",
        "supported_data_classes": ALL_DATA_CLASSES,
        "supported_source_kinds": frozenset({"channel", "memory", "tool_result"}),
        "supported_purposes": ALL_PURPOSES,
        "retention": ModelRetention.ZERO_RETENTION,
        "allow_raw_content": True,
        "routing_priority": 10,
        "active": True,
    }
    values.update(changes)
    return ModelProfile(**values)  # type: ignore[arg-type]


def _policy(**changes: object) -> ModelEgressPolicy:
    values: dict[str, object] = {
        "policy_id": "root-default-egress",
        "policy_version": "policy-v1",
        "profile": "root-default",
        "allowed_providers": frozenset({"openai", "bedrock"}),
        "allowed_models": frozenset({"gpt-5.6", "claude-sonnet"}),
        "allowed_regions": frozenset({"us-east-1", "ap-northeast-2"}),
        "allowed_data_classes": ALL_DATA_CLASSES,
        "allowed_source_kinds": frozenset({"channel", "memory", "tool_result"}),
        "allowed_purposes": ALL_PURPOSES,
        "require_redaction": True,
        "require_zero_retention": True,
        "allow_raw_content": True,
    }
    values.update(changes)
    return ModelEgressPolicy(**values)  # type: ignore[arg-type]


def _services(
    *,
    candidates: tuple[ModelProfile, ...] | None = None,
    policy: ModelEgressPolicy | None | object = ...,
    provider: RecordingModelProvider | None = None,
    recorder: RecordingInvocationRecorder | None = None,
) -> tuple[
    ModelPolicyService,
    GuardedModelExecutionService,
    StubProfileProvider,
    StubPolicyProvider,
    RecordingModelProvider,
]:
    profiles = StubProfileProvider(candidates if candidates is not None else (_profile(),))
    policies = StubPolicyProvider(_policy() if policy is ... else policy)  # type: ignore[arg-type]
    recording_provider = provider or RecordingModelProvider()
    service = ModelPolicyService(
        profiles=profiles,
        policies=policies,
        redactor=RedactionService(core_secret_redaction_policy()),
    )
    execution = GuardedModelExecutionService(
        service,
        provider=recording_provider,
        output_validator=JsonSchemaOutputValidator(),
        invocations=recorder or RecordingInvocationRecorder(),
    )
    return service, execution, profiles, policies, recording_provider


def _blocked(
    service: ModelPolicyService,
    request: ModelCallRequest | None = None,
) -> ModelPolicyBlockedError:
    with pytest.raises(ModelPolicyBlockedError) as captured:
        asyncio.run(service.guard(request or _request()))
    return captured.value


def test_profile_policy_and_schema_fingerprints_are_canonical() -> None:
    profile = _profile(
        supported_data_classes=frozenset(reversed(tuple(DataClass))),
        supported_purposes=frozenset(reversed(tuple(ModelPurpose))),
    )
    policy = _policy(
        allowed_providers=frozenset({"bedrock", "openai"}),
        allowed_models=frozenset({"claude-sonnet", "gpt-5.6"}),
    )
    schema = StructuredOutputSchema(
        name="agent-result-v1",
        canonical_schema_json='{ "type": "object", "required": ["answer"] }',
    )

    assert profile.fingerprint == _profile().fingerprint
    assert policy.fingerprint == _policy().fingerprint
    assert schema.canonical_schema_json == '{"required":["answer"],"type":"object"}'
    assert len(schema.fingerprint) == 64
    snapshot = PolicyImpactSnapshot((profile.impact_reference(), policy.impact_reference()))
    assert tuple(reference.key for reference in snapshot.policies) == (
        "model.egress:root-default-egress",
        "model.profile:root-openai-primary",
    )


def test_allowed_call_aggregates_classification_selects_priority_and_redacts() -> None:
    fallback = _profile(
        profile_id="root-bedrock-fallback",
        provider="bedrock",
        model="claude-sonnet",
        region="ap-northeast-2",
        routing_priority=20,
    )
    primary = _profile(routing_priority=10)
    _, execution, _, _, provider = _services(candidates=(fallback, primary))
    secret = "sk-private-token-12345"
    request = _request(
        sources=(
            _source(data_classes=frozenset({DataClass.PUBLIC}), content="public context"),
            _source(
                source_kind="memory",
                data_classes=frozenset({DataClass.PERSONAL}),
                content=f"authorization=Bearer {secret}",
                canonical_data_json='{"password":"field-secret","safe":"kept"}',
            ),
        )
    )

    result = asyncio.run(execution.execute(request, context=CONTEXT))

    assert result.decision.outcome is ModelPolicyOutcome.ALLOWED
    assert result.decision.data_classes == (DataClass.PUBLIC, DataClass.PERSONAL)
    assert result.decision.highest_data_class is DataClass.PERSONAL
    assert result.decision.source_kinds == ("channel", "memory")
    assert result.decision.selected_profile_id == primary.profile_id
    assert result.decision.eligible_candidate_count == 2
    assert result.decision.redaction is not None
    assert result.decision.redaction.redaction_count == 2
    assert len(provider.calls) == 1
    assert secret not in provider.calls[0].sources[1].content
    assert "[REDACTED]" in provider.calls[0].sources[1].content
    assert provider.calls[0].sources[1].canonical_data_json == (
        '{"password":"[REDACTED]","safe":"kept"}'
    )
    assert provider.calls[0].input_fingerprint == result.decision.input_fingerprint


@pytest.mark.parametrize(
    ("policy_changes", "profile_changes", "call_request"),
    (
        ({"allowed_providers": frozenset({"bedrock"})}, {}, _request()),
        ({"allowed_models": frozenset({"other-model"})}, {}, _request()),
        ({"allowed_regions": frozenset({"ap-northeast-2"})}, {}, _request()),
        (
            {"allowed_purposes": frozenset({ModelPurpose.SKILL})},
            {},
            _request(),
        ),
        (
            {},
            {"supported_purposes": frozenset({ModelPurpose.SKILL})},
            _request(),
        ),
        (
            {"allowed_source_kinds": frozenset({"memory"})},
            {},
            _request(),
        ),
        (
            {},
            {"supported_source_kinds": frozenset({"memory"})},
            _request(),
        ),
        (
            {"allowed_data_classes": frozenset({DataClass.PUBLIC})},
            {},
            _request(),
        ),
        (
            {},
            {"supported_data_classes": frozenset({DataClass.PUBLIC})},
            _request(),
        ),
        ({}, {"retention": ModelRetention.PROVIDER_DEFAULT}, _request()),
        (
            {"allow_raw_content": False},
            {},
            _request(sources=(_source(raw_content=True),)),
        ),
        (
            {"allow_raw_content": True},
            {"allow_raw_content": False},
            _request(sources=(_source(raw_content=True),)),
        ),
    ),
)
def test_provider_model_region_purpose_source_data_retention_and_raw_matrix_denies(
    policy_changes: dict[str, object],
    profile_changes: dict[str, object],
    call_request: ModelCallRequest,
) -> None:
    service, _, _, _, provider = _services(
        policy=_policy(**policy_changes),
        candidates=(_profile(**profile_changes),),
    )

    denied = _blocked(service, call_request)

    assert denied.code is ModelPolicyErrorCode.POLICY_DENIED
    assert denied.decision.stage.value == "candidate"
    assert denied.decision.eligible_candidate_count == 0
    assert provider.calls == []


def test_regionless_profile_requires_an_explicit_regionless_policy() -> None:
    regionless = _profile(region=None)
    service, _, _, _, _ = _services(
        candidates=(regionless,),
        policy=_policy(allowed_regions=frozenset()),
    )

    allowed = asyncio.run(service.guard(_request()))
    assert allowed.profile.region is None

    service, _, _, _, provider = _services(candidates=(regionless,))
    denied = _blocked(service)
    assert denied.code is ModelPolicyErrorCode.POLICY_DENIED
    assert provider.calls == []


def test_redaction_cannot_be_disabled_by_an_egress_policy() -> None:
    _, execution, _, _, provider = _services(policy=_policy(require_redaction=False))
    secret = "sk-policy-cannot-disable-redaction"

    asyncio.run(
        execution.execute(
            _request(sources=(_source(content=secret),)),
            context=CONTEXT,
        )
    )

    assert provider.calls[0].sources[0].content == "[REDACTED]"


def test_missing_policy_empty_candidates_and_ambiguous_priority_fail_closed() -> None:
    service, _, profiles, policies, provider = _services(policy=None)
    missing = _blocked(service)

    assert missing.code is ModelPolicyErrorCode.POLICY_MISSING
    assert profiles.calls == []
    assert policies.calls == ["root-default"]
    assert provider.calls == []

    service, _, _, _, provider = _services(candidates=())
    empty = _blocked(service)
    assert empty.code is ModelPolicyErrorCode.POLICY_DENIED
    assert empty.decision.evaluated_candidate_count == 0
    assert provider.calls == []

    duplicate_priority = _profile(
        profile_id="root-openai-secondary",
        routing_priority=10,
    )
    service, _, _, _, provider = _services(candidates=(_profile(), duplicate_priority))
    ambiguous = _blocked(service)
    assert ambiguous.code is ModelPolicyErrorCode.POLICY_DENIED
    assert provider.calls == []


def test_request_response_failures_and_representations_do_not_expose_content() -> None:
    prompt_secret = "sk-private-prompt-12345"
    schema_secret = "private-schema-field"
    request = ModelCallRequest(
        logical_call_id="logical-call-secret",
        profile="root-default",
        purpose=ModelPurpose.ORCHESTRATION,
        sources=(_source(content=prompt_secret),),
        output_schema=StructuredOutputSchema(
            name="secret-schema-v1",
            canonical_schema_json=f'{{"properties":{{"{schema_secret}":{{"type":"string"}}}}}}',
        ),
    )

    assert prompt_secret not in repr(request)
    assert schema_secret not in repr(request)

    output_secret = "private-provider-output"
    response = ModelProviderResponse(f'{{"answer":"{output_secret}"}}')
    assert output_secret not in repr(response)
    assert len(response.output_fingerprint) == 64

    with pytest.raises(ValueError) as captured:
        ModelProviderResponse(output_secret)
    assert output_secret not in str(captured.value)
    assert output_secret not in repr(captured.value)

    failure = ModelProviderFailure(ModelProviderErrorCode.TIMEOUT, retryable=True)
    assert failure.code is ModelProviderErrorCode.TIMEOUT
    assert failure.retryable

    invalid_code_secret = "private-provider-error"
    with pytest.raises(ValueError) as captured:
        ModelProviderFailure(invalid_code_secret, retryable=True)  # type: ignore[arg-type]
    assert invalid_code_secret not in str(captured.value)


def test_invocation_persistence_failures_never_duplicate_provider_calls() -> None:
    start_failure = FailingInvocationRecorder(fail_start=True)
    _, execution, _, _, provider = _services(recorder=start_failure)

    with pytest.raises(ModelInvocationPersistenceError):
        asyncio.run(execution.execute(_request(), context=CONTEXT))
    assert provider.calls == []

    finish_failure = FailingInvocationRecorder(fail_finish=True)
    _, execution, _, _, provider = _services(recorder=finish_failure)

    with pytest.raises(ModelInvocationPersistenceError):
        asyncio.run(execution.execute(_request(), context=CONTEXT))
    assert len(provider.calls) == 1
    assert len(finish_failure.started) == len(finish_failure.finished) == 1


def test_contracts_reject_mutable_or_invalid_collections() -> None:
    with pytest.raises(ValueError, match="immutable frozenset"):
        _source(data_classes={DataClass.PUBLIC})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="immutable tuple"):
        ModelCallRequest(
            logical_call_id="logical-call-0001",
            profile="root-default",
            purpose=ModelPurpose.ORCHESTRATION,
            sources=[_source()],  # type: ignore[arg-type]
            output_schema=_schema(),
        )
    with pytest.raises(ValueError, match="routing_priority"):
        _profile(routing_priority=-1)
    with pytest.raises(ValueError, match="allowed_regions"):
        _policy(allowed_regions={"us-east-1"})
