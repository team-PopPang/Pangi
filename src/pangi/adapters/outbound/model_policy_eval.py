"""Fail-closed WBS-15 boundary used until the Eval runtime is composed."""

from pangi.application.contracts.model_policy_management import (
    ModelPolicyEvaluation,
    ModelPolicyImpact,
)
from pangi.application.ports.model_policy_management import (
    ModelPolicyEvalUnavailableError,
)


class UnavailableModelPolicyEvaluationGateway:
    """Reject every request instead of pretending that an Eval passed."""

    async def request_evaluation(
        self,
        *,
        actor_id: str,
        impact: ModelPolicyImpact,
        idempotency_key: str,
    ) -> ModelPolicyEvaluation:
        del actor_id, impact, idempotency_key
        raise ModelPolicyEvalUnavailableError("The Eval runtime is not available")

    async def require_approved(
        self,
        *,
        eval_run_id: str,
        impact: ModelPolicyImpact,
    ) -> None:
        del eval_run_id, impact
        raise ModelPolicyEvalUnavailableError("The Eval runtime is not available")
