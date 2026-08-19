"""Administrator-only Model Policy query, Eval, and activation use cases."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.model_policy_management import (
    ModelPolicyActivation,
    ModelPolicyActivationCommand,
    ModelPolicyCursorPosition,
    ModelPolicyEvaluation,
    ModelPolicyListPage,
    ModelPolicyListQuery,
    ModelPolicyStoreQuery,
    ModelPolicyVersion,
    compare_model_policy_versions,
)
from pangi.application.ports.auth import PermissionDeniedError
from pangi.application.ports.model_policy_management import (
    InvalidModelPolicyCursorError,
    ModelPolicyConflictError,
    ModelPolicyEvaluationGateway,
    ModelPolicyManagementStore,
    ModelPolicyNotFoundError,
    ModelPolicyStaleImpactError,
)
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.model_routing import ModelPolicyState

Clock = Callable[[], datetime]

_CURSOR_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUMMARY_WINDOW = timedelta(days=7)
_IDEMPOTENCY_TTL = timedelta(hours=24)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _request_fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _query_fingerprint(actor: AuthenticatedPrincipal) -> str:
    return _request_fingerprint(
        {
            "actor_role": actor.role.value,
            "actor_user_id": actor.user_id,
            "resource": "model-policies",
        }
    )


def _encode_cursor(
    position: ModelPolicyCursorPosition,
    *,
    query_fingerprint: str,
) -> str:
    payload = _canonical_json(
        {
            "created_at": position.created_at.astimezone(UTC).isoformat(),
            "policy_id": position.policy_id,
            "query_fingerprint": query_fingerprint,
            "version": _CURSOR_VERSION,
            "policy_version": position.version,
        }
    ).encode()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(
    cursor: str,
    *,
    query_fingerprint: str,
) -> ModelPolicyCursorPosition:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode())
        if not isinstance(payload, dict) or set(payload) != {
            "created_at",
            "policy_id",
            "policy_version",
            "query_fingerprint",
            "version",
        }:
            raise ValueError
        if payload["version"] != _CURSOR_VERSION:
            raise ValueError
        if payload["query_fingerprint"] != query_fingerprint:
            raise ValueError
        created_at_value = payload["created_at"]
        policy_id = payload["policy_id"]
        policy_version = payload["policy_version"]
        if not all(isinstance(item, str) for item in (created_at_value, policy_id, policy_version)):
            raise ValueError
        assert isinstance(created_at_value, str)
        assert isinstance(policy_id, str)
        assert isinstance(policy_version, str)
        if _IDENTIFIER.fullmatch(policy_id) is None:
            raise ValueError
        if _IDENTIFIER.fullmatch(policy_version) is None:
            raise ValueError
        created_at = datetime.fromisoformat(created_at_value)
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise InvalidModelPolicyCursorError("The Model Policy cursor is invalid") from error
    return ModelPolicyCursorPosition(created_at.astimezone(UTC), policy_id, policy_version)


class ModelPolicyManagementService:
    """Coordinate safe Model Policy administration without owning Eval execution."""

    def __init__(
        self,
        store: ModelPolicyManagementStore,
        evaluation_gateway: ModelPolicyEvaluationGateway,
        *,
        clock: Clock = _utc_now,
        summary_window: timedelta = _SUMMARY_WINDOW,
        idempotency_ttl: timedelta = _IDEMPOTENCY_TTL,
    ) -> None:
        if summary_window <= timedelta(0):
            raise ValueError("summary_window must be positive")
        if idempotency_ttl <= timedelta(0):
            raise ValueError("idempotency_ttl must be positive")
        self._store = store
        self._evaluation_gateway = evaluation_gateway
        self._clock = clock
        self._summary_window = summary_window
        self._idempotency_ttl = idempotency_ttl

    async def list_policies(
        self,
        *,
        actor: AuthenticatedPrincipal,
        query: ModelPolicyListQuery,
    ) -> ModelPolicyListPage:
        self._require_admin(actor)
        fingerprint = _query_fingerprint(actor)
        after = (
            _decode_cursor(query.cursor, query_fingerprint=fingerprint)
            if query.cursor is not None
            else None
        )
        ended_at = self._clock().astimezone(UTC)
        fetched = await self._store.list_management_items(
            ModelPolicyStoreQuery(
                limit=query.limit + 1,
                after=after,
                summary_started_at=ended_at - self._summary_window,
                summary_ended_at=ended_at,
            )
        )
        items = fetched[: query.limit]
        next_cursor = None
        if len(fetched) > query.limit and items:
            last = items[-1].policy
            next_cursor = _encode_cursor(
                ModelPolicyCursorPosition(last.created_at, last.policy_id, last.version),
                query_fingerprint=fingerprint,
            )
        return ModelPolicyListPage(items, next_cursor)

    async def evaluate_policy(
        self,
        *,
        actor: AuthenticatedPrincipal,
        policy_id: str,
        version: str,
        candidate_fingerprint: str,
        idempotency_key: str,
    ) -> ModelPolicyEvaluation:
        self._require_admin(actor)
        self._validate_request(
            policy_id=policy_id,
            version=version,
            candidate_fingerprint=candidate_fingerprint,
            idempotency_key=idempotency_key,
        )
        candidate, baseline = await self._candidate_and_baseline(policy_id, version)
        if candidate.fingerprint != candidate_fingerprint:
            raise ModelPolicyStaleImpactError("The candidate Policy fingerprint changed")
        impact = compare_model_policy_versions(baseline, candidate)
        return await self._evaluation_gateway.request_evaluation(
            actor_id=actor.user_id,
            impact=impact,
            idempotency_key=idempotency_key,
        )

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
        self._require_admin(actor)
        self._validate_request(
            policy_id=policy_id,
            version=version,
            candidate_fingerprint=candidate_fingerprint,
            idempotency_key=idempotency_key,
        )
        if _SHA256.fullmatch(impact_fingerprint) is None:
            raise ValueError("impact_fingerprint must be a SHA-256 hex digest")
        if not 16 <= len(eval_run_id) <= 64 or eval_run_id.strip() != eval_run_id:
            raise ValueError("eval_run_id must contain 16-64 non-padding characters")
        recorded_at = self._clock().astimezone(UTC)
        semantic_request = {
            "candidate_fingerprint": candidate_fingerprint,
            "eval_run_id": eval_run_id,
            "impact_fingerprint": impact_fingerprint,
            "policy_id": policy_id,
            "version": version,
        }
        request_fingerprint = _request_fingerprint(semantic_request)
        replay = await self._store.get_activation_replay(
            actor_id=actor.user_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            recorded_at=recorded_at,
        )
        if replay is not None:
            return replay
        candidate, baseline = await self._candidate_and_baseline(policy_id, version)
        if candidate.fingerprint != candidate_fingerprint:
            raise ModelPolicyStaleImpactError("The candidate Policy fingerprint changed")
        impact = compare_model_policy_versions(baseline, candidate)
        if impact.fingerprint != impact_fingerprint:
            raise ModelPolicyStaleImpactError("The Model Policy impact changed")
        await self._evaluation_gateway.require_approved(
            eval_run_id=eval_run_id,
            impact=impact,
        )
        return await self._store.activate(
            ModelPolicyActivationCommand(
                actor_id=actor.user_id,
                policy_id=policy_id,
                version=version,
                candidate_fingerprint=candidate_fingerprint,
                impact_fingerprint=impact_fingerprint,
                eval_run_id=eval_run_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                recorded_at=recorded_at,
                expires_at=recorded_at + self._idempotency_ttl,
                expected_baseline_fingerprint=(None if baseline is None else baseline.fingerprint),
            )
        )

    async def _candidate_and_baseline(
        self,
        policy_id: str,
        version: str,
    ) -> tuple[ModelPolicyVersion, ModelPolicyVersion | None]:
        candidate = await self._store.get_version(policy_id, version)
        if candidate is None:
            raise ModelPolicyNotFoundError("The Model Policy version was not found")
        if candidate.state is not ModelPolicyState.DRAFT:
            raise ModelPolicyConflictError("Only a draft Model Policy can be evaluated")
        baseline = await self._store.get_active_version(candidate.profile)
        return candidate, baseline

    @staticmethod
    def _require_admin(actor: AuthenticatedPrincipal) -> None:
        if actor.role is not UserRole.ADMIN or actor.status is not UserStatus.ACTIVE:
            raise PermissionDeniedError("The authenticated role is not allowed")

    @staticmethod
    def _validate_request(
        *,
        policy_id: str,
        version: str,
        candidate_fingerprint: str,
        idempotency_key: str,
    ) -> None:
        if _IDENTIFIER.fullmatch(policy_id) is None:
            raise ValueError("policy_id must be a stable identifier")
        if _IDENTIFIER.fullmatch(version) is None:
            raise ValueError("version must be a stable identifier")
        if _SHA256.fullmatch(candidate_fingerprint) is None:
            raise ValueError("candidate_fingerprint must be a SHA-256 hex digest")
        if not 1 <= len(idempotency_key) <= 255:
            raise ValueError("idempotency_key must contain 1-255 characters")
