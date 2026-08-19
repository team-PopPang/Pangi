"""Single-call Root orchestration service tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from pangi.application.contracts.guardrails import GuardedRunRequest, GuardrailDecision
from pangi.application.contracts.model_persistence import ModelInvocationContext
from pangi.application.contracts.model_routing import (
    GuardedModelExecution,
    ModelCallRequest,
    ModelPolicyDecision,
    ModelProviderFailure,
    ModelProviderResponse,
)
from pangi.application.contracts.orchestration import OrchestratorLimits
from pangi.application.contracts.redaction import RedactionSummary
from pangi.application.contracts.root_orchestration import (
    RootCatalogSnapshot,
    RootOrchestrationRequest,
    RootOrchestratorPolicy,
    RootSkillDescriptor,
    RootSubagentDescriptor,
)
from pangi.application.services.plan_validator import (
    PlanValidationError,
    PlanValidationErrorCode,
)
from pangi.application.services.root_context import RootDecisionParseError
from pangi.application.services.root_orchestrator import (
    RootCatalogUnavailableError,
    RootOrchestratorService,
    root_logical_call_id,
)
from pangi.domain.auth import UserRole
from pangi.domain.guardrails import GuardrailOutcome, GuardrailStage, TrustLevel
from pangi.domain.model_routing import (
    DataClass,
    ModelPolicyOutcome,
    ModelPolicyStage,
    ModelProviderErrorCode,
    ModelPurpose,
)
from pangi.domain.runs import Principal, PrincipalChannel, RunMode, RunRequest

NOW = datetime(2030, 1, 1, tzinfo=UTC)
RUN_ID = "run-root-service-0001"


class RecordingCatalogProvider:
    def __init__(
        self,
        snapshot: RootCatalogSnapshot | None,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.failure = failure
        self.calls: list[Principal] = []

    async def snapshot_for(self, principal: Principal) -> RootCatalogSnapshot:
        self.calls.append(principal)
        if self.failure is not None:
            raise self.failure
        if self.snapshot is None:
            raise RuntimeError("missing snapshot")
        return self.snapshot


class RecordingRootModel:
    def __init__(
        self,
        execution: GuardedModelExecution | None,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.execution = execution
        self.failure = failure
        self.calls: list[tuple[ModelCallRequest, ModelInvocationContext]] = []

    async def execute(
        self,
        request: ModelCallRequest,
        *,
        context: ModelInvocationContext,
    ) -> GuardedModelExecution:
        self.calls.append((request, context))
        if self.failure is not None:
            raise self.failure
        if self.execution is None:
            raise RuntimeError("missing execution")
        return self.execution


def _catalog() -> RootCatalogSnapshot:
    return RootCatalogSnapshot(
        version="catalog-v1",
        subagents=(
            RootSubagentDescriptor("github-research", "Search GitHub."),
            RootSubagentDescriptor("notion-research", "Search Notion."),
            RootSubagentDescriptor("synthesis", "Compare standard results."),
        ),
        skills=(RootSkillDescriptor("weekly-summary", "Build a weekly summary."),),
        connection_names=("github-primary", "notion-primary"),
    )


def _policy() -> RootOrchestratorPolicy:
    return RootOrchestratorPolicy(
        profile="root-default",
        prompt_version="root-orchestration-v1",
        limits=OrchestratorLimits(),
    )


def _guardrail_decision() -> GuardrailDecision:
    return GuardrailDecision(
        trust_level=TrustLevel.UNTRUSTED,
        stage=GuardrailStage.COMPLETE,
        outcome=GuardrailOutcome.ALLOWED,
        policy_version="input-v1",
        policy_fingerprint="a" * 64,
        unicode_policy_version="unicode-v1",
        text_bytes=20,
    )


def _request(
    *,
    explicit_skill: str | None = None,
    schedule_id: str | None = None,
) -> RootOrchestrationRequest:
    run_request = RunRequest(
        request_id="request-root-0001",
        principal=Principal(
            "principal-root-0001",
            UserRole.MEMBER,
            PrincipalChannel.SCHEDULER if schedule_id is not None else PrincipalChannel.API,
        ),
        text="Summarize the current work.",
        idempotency_key="idempotency-root-0001",
        created_at=NOW,
        explicit_skill=explicit_skill,
        schedule_id=schedule_id,
    )
    return RootOrchestrationRequest(
        run_id=RUN_ID,
        guarded_request=GuardedRunRequest(run_request, _guardrail_decision()),
        data_classes=frozenset({DataClass.INTERNAL}),
    )


def _decision_json(
    *,
    mode: RunMode = RunMode.DIRECT,
    subagent: str = "github-research",
) -> str:
    payload: dict[str, object] = {
        "composition": "deterministic",
        "direct_answer": None,
        "mode": mode.value,
        "skill_name": None,
        "tasks": [],
        "user_message": None,
    }
    if mode is RunMode.DIRECT:
        payload["direct_answer"] = "The current work is summarized."
    elif mode is RunMode.SKILL:
        payload["skill_name"] = "weekly-summary"
    else:
        payload["tasks"] = [
            {
                "allowed_tool_hints": [],
                "connection_hints": ["github-primary"],
                "depends_on": [],
                "id": "collect-work",
                "objective": "Collect the current work.",
                "subagent": subagent,
                "timeout_seconds": 60,
            }
        ]
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _execution(
    output: str,
    *,
    provider_request_count: int = 1,
) -> GuardedModelExecution:
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
        policy_id="root-egress-v1",
        policy_version="policy-v1",
        policy_fingerprint="b" * 64,
        selected_profile_id="root-openai-v1",
        selected_profile_fingerprint="c" * 64,
        provider="openai",
        model="test-model",
        redaction=RedactionSummary(
            policy_version="redaction-v1",
            policy_fingerprint="d" * 64,
            redaction_count=0,
            applied_rule_ids=(),
        ),
        input_fingerprint="e" * 64,
    )
    return GuardedModelExecution(
        response=ModelProviderResponse(
            output,
            provider_request_count=provider_request_count,
        ),
        decision=decision,
    )


def _service(
    *,
    catalog: RecordingCatalogProvider | None = None,
    model: RecordingRootModel | None = None,
) -> tuple[RootOrchestratorService, RecordingCatalogProvider, RecordingRootModel]:
    resolved_catalog = catalog or RecordingCatalogProvider(_catalog())
    resolved_model = model or RecordingRootModel(_execution(_decision_json()))
    return (
        RootOrchestratorService(
            _policy(),
            catalogs=resolved_catalog,
            model=resolved_model,
        ),
        resolved_catalog,
        resolved_model,
    )


@pytest.mark.parametrize("schedule_id", (None, "schedule-root-0001"))
def test_natural_requests_make_one_logical_call_even_with_transport_retries(
    schedule_id: str | None,
) -> None:
    model = RecordingRootModel(_execution(_decision_json(), provider_request_count=3))
    service, catalogs, _ = _service(model=model)

    result = asyncio.run(service.decide(_request(schedule_id=schedule_id)))

    assert result.plan.decision.mode is RunMode.DIRECT
    assert result.logical_call_count == 1
    assert result.provider_request_count == 3
    assert len(catalogs.calls) == 1
    assert len(model.calls) == 1
    model_request, context = model.calls[0]
    assert model_request.logical_call_id == f"root-orchestration:{RUN_ID}"
    assert context == ModelInvocationContext(RUN_ID)


def test_explicit_skill_uses_the_same_snapshot_without_calling_the_model() -> None:
    service, catalogs, model = _service()

    result = asyncio.run(service.decide(_request(explicit_skill="weekly-summary")))

    assert result.plan.decision.mode is RunMode.SKILL
    assert result.plan.decision.skill_name == "weekly-summary"
    assert result.logical_call_count == 0
    assert result.provider_request_count == 0
    assert len(catalogs.calls) == 1
    assert model.calls == []

    with pytest.raises(PlanValidationError) as captured:
        asyncio.run(service.decide(_request(explicit_skill="unknown-skill")))
    assert captured.value.code is PlanValidationErrorCode.UNKNOWN_SKILL
    assert model.calls == []


def test_catalog_failure_is_safe_and_prevents_model_execution() -> None:
    catalog_secret = "private-catalog-failure"
    catalogs = RecordingCatalogProvider(
        None,
        failure=RuntimeError(catalog_secret),
    )
    service, _, model = _service(catalog=catalogs)

    with pytest.raises(RootCatalogUnavailableError) as captured:
        asyncio.run(service.decide(_request()))

    assert captured.value.code == "root_catalog_unavailable"
    assert catalog_secret not in str(captured.value)
    assert catalog_secret not in repr(captured.value)
    assert model.calls == []


def test_invalid_model_output_and_plan_never_trigger_a_semantic_retry() -> None:
    invalid_output_model = RecordingRootModel(_execution('{"invalid":"shape"}'))
    service, _, _ = _service(model=invalid_output_model)

    with pytest.raises(RootDecisionParseError):
        asyncio.run(service.decide(_request()))
    assert len(invalid_output_model.calls) == 1

    invalid_plan_model = RecordingRootModel(
        _execution(_decision_json(mode=RunMode.DELEGATE, subagent="unknown-agent"))
    )
    service, _, _ = _service(model=invalid_plan_model)

    with pytest.raises(PlanValidationError) as captured:
        asyncio.run(service.decide(_request()))
    assert captured.value.code is PlanValidationErrorCode.UNKNOWN_SUBAGENT
    assert len(invalid_plan_model.calls) == 1


def test_model_failure_is_not_retried_by_the_root_service() -> None:
    failure = ModelProviderFailure(
        ModelProviderErrorCode.TIMEOUT,
        retryable=True,
        provider_request_count=2,
    )
    model = RecordingRootModel(None, failure=failure)
    service, _, _ = _service(model=model)

    with pytest.raises(ModelProviderFailure) as captured:
        asyncio.run(service.decide(_request()))

    assert captured.value is failure
    assert len(model.calls) == 1


def test_root_logical_call_identity_is_stable_and_request_scoped() -> None:
    assert root_logical_call_id(RUN_ID) == root_logical_call_id(RUN_ID)
    assert root_logical_call_id(RUN_ID) != root_logical_call_id("run-root-service-0002")
