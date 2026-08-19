"""Root orchestration integration with the governed Model execution boundary."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from pangi.adapters.outbound.model_providers.json_schema import JsonSchemaOutputValidator
from pangi.application.contracts.guardrails import GuardedRunRequest, GuardrailDecision
from pangi.application.contracts.model_persistence import (
    ModelInvocationDenial,
    ModelInvocationFinish,
    ModelInvocationStart,
)
from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelEgressPolicy,
    ModelProfile,
    ModelProviderResponse,
)
from pangi.application.contracts.root_orchestration import (
    RootCatalogSnapshot,
    RootOrchestrationRequest,
    RootOrchestratorPolicy,
)
from pangi.application.services.model_routing import (
    GuardedModelExecutionService,
    ModelPolicyService,
)
from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)
from pangi.application.services.root_orchestrator import RootOrchestratorService
from pangi.domain.auth import UserRole
from pangi.domain.guardrails import GuardrailOutcome, GuardrailStage, TrustLevel
from pangi.domain.model_routing import (
    DataClass,
    ModelPurpose,
    ModelRetention,
)
from pangi.domain.runs import Principal, PrincipalChannel, RunMode, RunRequest

NOW = datetime(2030, 1, 1, tzinfo=UTC)
RUN_ID = "run-root-contract-0001"


class ContractProfiles:
    async def list_candidates(self, profile: str) -> tuple[ModelProfile, ...]:
        assert profile == "root-default"
        return (_profile(),)


class ContractPolicies:
    async def get_policy(self, profile: str) -> ModelEgressPolicy | None:
        assert profile == "root-default"
        return _egress_policy()


class ContractProvider:
    def __init__(self) -> None:
        self.calls: list[GuardedModelRequest] = []

    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        self.calls.append(request)
        output = {
            "composition": "deterministic",
            "direct_answer": "The request was handled safely.",
            "mode": "direct",
            "skill_name": None,
            "tasks": [],
            "user_message": None,
        }
        return ModelProviderResponse(json.dumps(output, separators=(",", ":"), sort_keys=True))


class ContractInvocations:
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


class ContractCatalogs:
    def __init__(self) -> None:
        self.calls = 0

    async def snapshot_for(self, principal: Principal) -> RootCatalogSnapshot:
        assert principal.user_id == "principal-root-0001"
        self.calls += 1
        return RootCatalogSnapshot(version="catalog-v1")


def _profile() -> ModelProfile:
    return ModelProfile(
        profile_id="root-openai-v1",
        profile="root-default",
        profile_version="profile-v1",
        provider="openai",
        model="contract-model",
        region="us-east-1",
        supported_data_classes=frozenset({DataClass.INTERNAL}),
        supported_source_kinds=frozenset({"channel", "policy"}),
        supported_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        retention=ModelRetention.ZERO_RETENTION,
        allow_raw_content=True,
        routing_priority=1,
    )


def _egress_policy() -> ModelEgressPolicy:
    return ModelEgressPolicy(
        policy_id="root-egress-v1",
        policy_version="policy-v1",
        profile="root-default",
        allowed_providers=frozenset({"openai"}),
        allowed_models=frozenset({"contract-model"}),
        allowed_regions=frozenset({"us-east-1"}),
        allowed_data_classes=frozenset({DataClass.INTERNAL}),
        allowed_source_kinds=frozenset({"channel", "policy"}),
        allowed_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        require_redaction=True,
        require_zero_retention=True,
        allow_raw_content=True,
    )


def _request(secret: str) -> RootOrchestrationRequest:
    run_request = RunRequest(
        request_id="request-root-0001",
        principal=Principal(
            "principal-root-0001",
            UserRole.MEMBER,
            PrincipalChannel.API,
        ),
        text=f"Summarize {secret}",
        idempotency_key="idempotency-root-0001",
        created_at=NOW,
    )
    decision = GuardrailDecision(
        trust_level=TrustLevel.UNTRUSTED,
        stage=GuardrailStage.COMPLETE,
        outcome=GuardrailOutcome.ALLOWED,
        policy_version="input-v1",
        policy_fingerprint="a" * 64,
        unicode_policy_version="unicode-v1",
        text_bytes=len(run_request.text.encode()),
    )
    return RootOrchestrationRequest(
        run_id=RUN_ID,
        guarded_request=GuardedRunRequest(run_request, decision),
        data_classes=frozenset({DataClass.INTERNAL}),
    )


def test_root_service_reuses_model_policy_redaction_schema_and_invocation_boundary() -> None:
    secret = "sk-root-secret-123456789"
    provider = ContractProvider()
    invocations = ContractInvocations()
    catalogs = ContractCatalogs()
    model = GuardedModelExecutionService(
        ModelPolicyService(
            profiles=ContractProfiles(),
            policies=ContractPolicies(),
            redactor=RedactionService(core_secret_redaction_policy()),
        ),
        provider=provider,
        output_validator=JsonSchemaOutputValidator(),
        invocations=invocations,
        clock=lambda: NOW,
        id_factory=lambda: "model-invocation-0001",
    )
    service = RootOrchestratorService(
        RootOrchestratorPolicy(
            profile="root-default",
            prompt_version="root-orchestration-v1",
        ),
        catalogs=catalogs,
        model=model,
    )

    result = asyncio.run(service.decide(_request(secret)))

    assert result.plan.decision.mode is RunMode.DIRECT
    assert result.logical_call_count == 1
    assert catalogs.calls == 1
    assert len(provider.calls) == 1
    assert len(invocations.started) == len(invocations.finished) == 1
    assert invocations.denied == []
    assert invocations.started[0].context.run_id == RUN_ID
    assert secret not in provider.calls[0].sources[1].content
    assert secret not in (provider.calls[0].sources[1].canonical_data_json or "")
    assert "[REDACTED]" in (provider.calls[0].sources[1].canonical_data_json or "")
