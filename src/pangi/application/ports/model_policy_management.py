"""Ports and expected failures for Model Policy administration."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.model_policy_management import (
    ModelPolicyActivation,
    ModelPolicyActivationCommand,
    ModelPolicyEvaluation,
    ModelPolicyImpact,
    ModelPolicyListItem,
    ModelPolicyListPage,
    ModelPolicyListQuery,
    ModelPolicyStoreQuery,
    ModelPolicyVersion,
)


class ModelPolicyManagementError(RuntimeError):
    """Base class for stable, secret-safe management failures."""

    code = "model_policy_operation_failed"


class InvalidModelPolicyCursorError(ModelPolicyManagementError):
    code = "invalid_model_policy_cursor"


class ModelPolicyNotFoundError(ModelPolicyManagementError):
    code = "model_policy_not_found"


class ModelPolicyConflictError(ModelPolicyManagementError):
    code = "model_policy_conflict"


class ModelPolicyStaleImpactError(ModelPolicyConflictError):
    code = "model_policy_stale_impact"


class ModelPolicyEvalRequiredError(ModelPolicyConflictError):
    code = "model_policy_eval_required"


class ModelPolicyEvalRejectedError(ModelPolicyConflictError):
    code = "model_policy_eval_rejected"


class ModelPolicyEvalUnavailableError(ModelPolicyManagementError):
    code = "model_policy_eval_unavailable"


class ModelPolicyIdempotencyConflictError(ModelPolicyConflictError):
    code = "model_policy_idempotency_conflict"


class ModelPolicyPersistenceError(ModelPolicyManagementError):
    code = "model_policy_persistence_error"


class ModelPolicyManagementOperations(Protocol):
    async def list_policies(
        self,
        *,
        actor: AuthenticatedPrincipal,
        query: ModelPolicyListQuery,
    ) -> ModelPolicyListPage:
        """Return one administrator-scoped page of safe Policy summaries."""

        ...

    async def evaluate_policy(
        self,
        *,
        actor: AuthenticatedPrincipal,
        policy_id: str,
        version: str,
        candidate_fingerprint: str,
        idempotency_key: str,
    ) -> ModelPolicyEvaluation:
        """Request an Eval for the current impact through the WBS-15 boundary."""

        ...

    async def activate_policy(
        self,
        *,
        actor: AuthenticatedPrincipal,
        policy_id: str,
        version: str,
        candidate_fingerprint: str,
        impact_fingerprint: str,
        eval_run_id: str,
        idempotency_key: str,
    ) -> ModelPolicyActivation:
        """Activate only a candidate approved for the exact current impact."""

        ...


class ModelPolicyManagementStore(Protocol):
    async def list_management_items(
        self,
        query: ModelPolicyStoreQuery,
    ) -> tuple[ModelPolicyListItem, ...]:
        """Read at most query.limit safe Policy rows in stable keyset order."""

        ...

    async def get_version(self, policy_id: str, version: str) -> ModelPolicyVersion | None:
        """Load one immutable Policy version."""

        ...

    async def get_active_version(self, profile: str) -> ModelPolicyVersion | None:
        """Load the single active version for a logical Model Profile."""

        ...

    async def get_activation_replay(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        recorded_at: datetime,
    ) -> ModelPolicyActivation | None:
        """Return an exact completed replay or reject a conflicting key."""

        ...

    async def activate(self, command: ModelPolicyActivationCommand) -> ModelPolicyActivation:
        """Atomically activate, retire, audit, and persist idempotency state."""

        ...


class ModelPolicyEvaluationGateway(Protocol):
    async def request_evaluation(
        self,
        *,
        actor_id: str,
        impact: ModelPolicyImpact,
        idempotency_key: str,
    ) -> ModelPolicyEvaluation:
        """Request or replay one Eval owned by WBS-15."""

        ...

    async def require_approved(
        self,
        *,
        eval_run_id: str,
        impact: ModelPolicyImpact,
    ) -> None:
        """Return only when the Eval approved this exact impact."""

        ...
