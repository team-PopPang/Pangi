from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from pangi.adapters.outbound.model_policy_eval import (
    UnavailableModelPolicyEvaluationGateway,
)
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.model_persistence import ModelPolicySnapshot
from pangi.application.contracts.model_policy_management import (
    ModelInvocationSummary,
    ModelPolicyActivation,
    ModelPolicyEvaluation,
    ModelPolicyListItem,
    ModelPolicyListQuery,
    ModelPolicyStoreQuery,
    ModelPolicyVersion,
    compare_model_policy_versions,
)
from pangi.application.contracts.model_routing import ModelEgressPolicy, ModelProfile
from pangi.application.ports.auth import PermissionDeniedError
from pangi.application.ports.model_policy_management import (
    ModelPolicyEvalUnavailableError,
    ModelPolicyStaleImpactError,
)
from pangi.application.services.model_policy_management import (
    ModelPolicyManagementService,
)
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.model_routing import (
    DataClass,
    ModelPolicyState,
    ModelPurpose,
    ModelRetention,
)

NOW = datetime(2030, 1, 8, tzinfo=UTC)
ADMIN = AuthenticatedPrincipal(
    "admin-user-000001",
    "Admin",
    UserRole.ADMIN,
    UserStatus.ACTIVE,
)


def _snapshot(version: str, *, model: str = "gpt-5.6") -> ModelPolicySnapshot:
    profile = ModelProfile(
        profile_id="root-openai-primary",
        profile="root-default",
        profile_version=f"profile-{version}",
        provider="openai",
        model=model,
        region="us-east-1",
        supported_data_classes=frozenset({DataClass.INTERNAL}),
        supported_source_kinds=frozenset({"channel"}),
        supported_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        retention=ModelRetention.ZERO_RETENTION,
        allow_raw_content=False,
        routing_priority=1,
    )
    policy = ModelEgressPolicy(
        policy_id="root-default-egress",
        policy_version=version,
        profile="root-default",
        allowed_providers=frozenset({"openai"}),
        allowed_models=frozenset({model}),
        allowed_regions=frozenset({"us-east-1"}),
        allowed_data_classes=frozenset({DataClass.INTERNAL}),
        allowed_source_kinds=frozenset({"channel"}),
        allowed_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        require_redaction=True,
        require_zero_retention=True,
        allow_raw_content=False,
    )
    return ModelPolicySnapshot(policy, (profile,))


def _version(
    version: str,
    state: ModelPolicyState,
    *,
    model: str = "gpt-5.6",
) -> ModelPolicyVersion:
    return ModelPolicyVersion(_snapshot(version, model=model), state, None, NOW, NOW)


class Store:
    def __init__(self, baseline: ModelPolicyVersion | None, candidate: ModelPolicyVersion) -> None:
        self.baseline = baseline
        self.candidate = candidate
        self.queries: list[ModelPolicyStoreQuery] = []
        self.activations = []

    async def list_management_items(
        self,
        query: ModelPolicyStoreQuery,
    ) -> tuple[ModelPolicyListItem, ...]:
        self.queries.append(query)
        summary = ModelInvocationSummary(
            query.summary_started_at,
            query.summary_ended_at,
            3,
            1,
            (),
            (),
        )
        impact = compare_model_policy_versions(self.baseline, self.candidate)
        return (ModelPolicyListItem(self.candidate, summary, impact),)

    async def get_version(self, policy_id: str, version: str) -> ModelPolicyVersion | None:
        if (policy_id, version) == (self.candidate.policy_id, self.candidate.version):
            return self.candidate
        return None

    async def get_active_version(self, profile: str) -> ModelPolicyVersion | None:
        assert profile == self.candidate.profile
        return self.baseline

    async def get_activation_replay(
        self,
        *,
        actor_id,
        idempotency_key,
        request_fingerprint,
        recorded_at,
    ):
        del actor_id, idempotency_key, request_fingerprint, recorded_at
        return None

    async def activate(self, command):
        self.activations.append(command)
        return ModelPolicyActivation(
            ModelPolicyVersion(
                self.candidate.snapshot,
                ModelPolicyState.ACTIVE,
                command.eval_run_id,
                self.candidate.created_at,
                command.recorded_at,
            ),
            command.impact_fingerprint,
            False,
        )


class Gateway:
    def __init__(self) -> None:
        self.requests = []
        self.approvals = []

    async def request_evaluation(self, *, actor_id, impact, idempotency_key):
        self.requests.append((actor_id, impact, idempotency_key))
        return ModelPolicyEvaluation("eval-run-identifier-0001", "queued", impact)

    async def require_approved(self, *, eval_run_id, impact) -> None:
        self.approvals.append((eval_run_id, impact))


def test_initial_activation_impact_is_deterministic_and_explicit() -> None:
    candidate = _version("policy-v1", ModelPolicyState.DRAFT)

    first = compare_model_policy_versions(None, candidate)
    second = compare_model_policy_versions(None, candidate)

    assert first.fingerprint == second.fingerprint
    assert first.baseline_policy_fingerprint is None
    assert first.added_policy_keys == (
        "model.egress:root-default-egress",
        "model.profile:root-openai-primary",
    )
    assert first.consumer_resolution == "unavailable"


def test_service_forwards_exact_impact_to_eval_and_activation_gate() -> None:
    async def scenario() -> None:
        baseline = _version("policy-v1", ModelPolicyState.ACTIVE)
        candidate = _version("policy-v2", ModelPolicyState.DRAFT, model="gpt-5.7")
        store = Store(baseline, candidate)
        gateway = Gateway()
        service = ModelPolicyManagementService(store, gateway, clock=lambda: NOW)

        evaluation = await service.evaluate_policy(
            actor=ADMIN,
            policy_id=candidate.policy_id,
            version=candidate.version,
            candidate_fingerprint=candidate.fingerprint,
            idempotency_key="evaluate-once",
        )
        activation = await service.activate_policy(
            actor=ADMIN,
            policy_id=candidate.policy_id,
            version=candidate.version,
            candidate_fingerprint=candidate.fingerprint,
            impact_fingerprint=evaluation.impact.fingerprint,
            eval_run_id=evaluation.eval_run_id,
            idempotency_key="activate-once",
        )

        assert gateway.requests[0][2] == "evaluate-once"
        assert gateway.approvals == [(evaluation.eval_run_id, evaluation.impact)]
        assert store.activations[0].expected_baseline_fingerprint == baseline.fingerprint
        assert activation.policy.state is ModelPolicyState.ACTIVE

    asyncio.run(scenario())


def test_service_rejects_stale_impact_before_eval_approval() -> None:
    async def scenario() -> None:
        candidate = _version("policy-v2", ModelPolicyState.DRAFT)
        store = Store(_version("policy-v1", ModelPolicyState.ACTIVE), candidate)
        gateway = Gateway()
        service = ModelPolicyManagementService(store, gateway, clock=lambda: NOW)

        with pytest.raises(ModelPolicyStaleImpactError):
            await service.activate_policy(
                actor=ADMIN,
                policy_id=candidate.policy_id,
                version=candidate.version,
                candidate_fingerprint=candidate.fingerprint,
                impact_fingerprint="0" * 64,
                eval_run_id="eval-run-identifier-0001",
                idempotency_key="activate-once",
            )

        assert gateway.approvals == []
        assert store.activations == []

    asyncio.run(scenario())


def test_unavailable_eval_and_non_admin_fail_closed() -> None:
    async def scenario() -> None:
        candidate = _version("policy-v1", ModelPolicyState.DRAFT)
        store = Store(None, candidate)
        service = ModelPolicyManagementService(
            store,
            UnavailableModelPolicyEvaluationGateway(),
            clock=lambda: NOW,
        )

        with pytest.raises(ModelPolicyEvalUnavailableError):
            await service.evaluate_policy(
                actor=ADMIN,
                policy_id=candidate.policy_id,
                version=candidate.version,
                candidate_fingerprint=candidate.fingerprint,
                idempotency_key="evaluate-once",
            )
        with pytest.raises(PermissionDeniedError):
            await service.list_policies(
                actor=AuthenticatedPrincipal(
                    "member-user-00001",
                    "Member",
                    UserRole.MEMBER,
                    UserStatus.ACTIVE,
                ),
                query=ModelPolicyListQuery(),
            )

        assert store.activations == []

    asyncio.run(scenario())
