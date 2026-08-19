"""Contracts for safe Model Policy administration and Eval gating."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pangi.application.contracts.model_persistence import ModelPolicySnapshot
from pangi.application.contracts.policy_impact import (
    PolicyFingerprintReference,
    PolicyImpactSnapshot,
    compare_policy_impact,
)
from pangi.domain.model_routing import ModelPolicyState

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
MODEL_POLICY_IMPACT_SCHEMA_VERSION = "model-policy-impact-v1"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _identifier(value: str, *, field_name: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a stable identifier")


def _fingerprint(value: str, *, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True, slots=True)
class ModelPolicyVersion:
    """One persisted Policy version with no raw prompts or Provider responses."""

    snapshot: ModelPolicySnapshot
    state: ModelPolicyState
    eval_run_id: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, ModelPolicySnapshot):
            raise TypeError("snapshot must be a ModelPolicySnapshot")
        try:
            object.__setattr__(self, "state", ModelPolicyState(self.state))
        except ValueError as error:
            raise ValueError("Model Policy state is invalid") from error
        if self.eval_run_id is not None:
            if (
                not 16 <= len(self.eval_run_id) <= 64
                or self.eval_run_id.strip() != self.eval_run_id
            ):
                raise ValueError("eval_run_id must contain 16-64 non-padding characters")
        object.__setattr__(self, "created_at", _utc(self.created_at, field_name="created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, field_name="updated_at"))

    @property
    def policy_id(self) -> str:
        return self.snapshot.policy.policy_id

    @property
    def version(self) -> str:
        return self.snapshot.policy.policy_version

    @property
    def profile(self) -> str:
        return self.snapshot.policy.profile

    @property
    def fingerprint(self) -> str:
        return self.snapshot.fingerprint

    @property
    def impact_references(self) -> tuple[PolicyFingerprintReference, ...]:
        return (
            self.snapshot.policy.impact_reference(),
            *(profile.impact_reference() for profile in self.snapshot.profiles),
        )


@dataclass(frozen=True, slots=True)
class ModelInvocationReasonCount:
    reason: str
    count: int

    def __post_init__(self) -> None:
        if not self.reason or len(self.reason) > 120:
            raise ValueError("reason must contain 1-120 characters")
        if self.count < 1:
            raise ValueError("count must be positive")


@dataclass(frozen=True, slots=True)
class ModelInvocationPurposeCount:
    purpose: str
    count: int

    def __post_init__(self) -> None:
        _identifier(self.purpose, field_name="purpose")
        if self.count < 1:
            raise ValueError("count must be positive")


@dataclass(frozen=True, slots=True)
class ModelInvocationSummary:
    """Bounded aggregate usage without request or response bodies."""

    window_started_at: datetime
    window_ended_at: datetime
    allowed_count: int
    denied_count: int
    purposes: tuple[ModelInvocationPurposeCount, ...]
    denial_reasons: tuple[ModelInvocationReasonCount, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "window_started_at",
            _utc(self.window_started_at, field_name="window_started_at"),
        )
        object.__setattr__(
            self,
            "window_ended_at",
            _utc(self.window_ended_at, field_name="window_ended_at"),
        )
        if self.window_started_at >= self.window_ended_at:
            raise ValueError("Invocation summary window must be positive")
        if self.allowed_count < 0 or self.denied_count < 0:
            raise ValueError("Invocation summary counts cannot be negative")
        if not isinstance(self.purposes, tuple) or not isinstance(self.denial_reasons, tuple):
            raise ValueError("Invocation summary collections must be immutable tuples")


@dataclass(frozen=True, slots=True)
class ModelPolicyImpact:
    """Safe deterministic impact between one active baseline and a candidate."""

    candidate_policy_id: str
    candidate_version: str
    candidate_policy_fingerprint: str
    candidate_snapshot_fingerprint: str
    baseline_policy_id: str | None
    baseline_version: str | None
    baseline_policy_fingerprint: str | None
    baseline_snapshot_fingerprint: str | None
    added_policy_keys: tuple[str, ...]
    removed_policy_keys: tuple[str, ...]
    changed_policy_keys: tuple[str, ...]
    consumer_resolution: Literal["unavailable"] = "unavailable"

    def __post_init__(self) -> None:
        _identifier(self.candidate_policy_id, field_name="candidate_policy_id")
        _identifier(self.candidate_version, field_name="candidate_version")
        _fingerprint(
            self.candidate_policy_fingerprint,
            field_name="candidate_policy_fingerprint",
        )
        _fingerprint(
            self.candidate_snapshot_fingerprint,
            field_name="candidate_snapshot_fingerprint",
        )
        baseline_values = (
            self.baseline_policy_id,
            self.baseline_version,
            self.baseline_policy_fingerprint,
            self.baseline_snapshot_fingerprint,
        )
        if any(value is None for value in baseline_values) is not all(
            value is None for value in baseline_values
        ):
            raise ValueError("baseline metadata must be present together")
        if self.baseline_policy_id is not None:
            assert self.baseline_version is not None
            assert self.baseline_policy_fingerprint is not None
            assert self.baseline_snapshot_fingerprint is not None
            _identifier(self.baseline_policy_id, field_name="baseline_policy_id")
            _identifier(self.baseline_version, field_name="baseline_version")
            _fingerprint(
                self.baseline_policy_fingerprint,
                field_name="baseline_policy_fingerprint",
            )
            _fingerprint(
                self.baseline_snapshot_fingerprint,
                field_name="baseline_snapshot_fingerprint",
            )
        collections = (
            self.added_policy_keys,
            self.removed_policy_keys,
            self.changed_policy_keys,
        )
        if any(values != tuple(sorted(set(values))) for values in collections):
            raise ValueError("impact policy keys must be unique sorted tuples")
        if not any(collections):
            raise ValueError("a candidate Model Policy impact must contain changes")

    @property
    def affected_policy_keys(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                (
                    *self.added_policy_keys,
                    *self.removed_policy_keys,
                    *self.changed_policy_keys,
                )
            )
        )

    @property
    def fingerprint(self) -> str:
        payload = {
            "added_policy_keys": list(self.added_policy_keys),
            "baseline_policy_fingerprint": self.baseline_policy_fingerprint,
            "baseline_snapshot_fingerprint": self.baseline_snapshot_fingerprint,
            "candidate_policy_fingerprint": self.candidate_policy_fingerprint,
            "candidate_snapshot_fingerprint": self.candidate_snapshot_fingerprint,
            "changed_policy_keys": list(self.changed_policy_keys),
            "removed_policy_keys": list(self.removed_policy_keys),
            "schema_version": MODEL_POLICY_IMPACT_SCHEMA_VERSION,
        }
        return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def compare_model_policy_versions(
    baseline: ModelPolicyVersion | None,
    candidate: ModelPolicyVersion,
) -> ModelPolicyImpact:
    """Compare safe Policy references and support a first activation explicitly."""

    candidate_snapshot = PolicyImpactSnapshot(candidate.impact_references)
    if baseline is None:
        return ModelPolicyImpact(
            candidate_policy_id=candidate.policy_id,
            candidate_version=candidate.version,
            candidate_policy_fingerprint=candidate.fingerprint,
            candidate_snapshot_fingerprint=candidate_snapshot.fingerprint,
            baseline_policy_id=None,
            baseline_version=None,
            baseline_policy_fingerprint=None,
            baseline_snapshot_fingerprint=None,
            added_policy_keys=tuple(reference.key for reference in candidate_snapshot.policies),
            removed_policy_keys=(),
            changed_policy_keys=(),
        )
    baseline_snapshot = PolicyImpactSnapshot(baseline.impact_references)
    diff = compare_policy_impact(baseline_snapshot, candidate_snapshot)
    if not diff.has_changes:
        raise ValueError("candidate Model Policy must differ from the active baseline")
    return ModelPolicyImpact(
        candidate_policy_id=candidate.policy_id,
        candidate_version=candidate.version,
        candidate_policy_fingerprint=candidate.fingerprint,
        candidate_snapshot_fingerprint=candidate_snapshot.fingerprint,
        baseline_policy_id=baseline.policy_id,
        baseline_version=baseline.version,
        baseline_policy_fingerprint=baseline.fingerprint,
        baseline_snapshot_fingerprint=baseline_snapshot.fingerprint,
        added_policy_keys=diff.added_policy_keys,
        removed_policy_keys=diff.removed_policy_keys,
        changed_policy_keys=diff.changed_policy_keys,
    )


@dataclass(frozen=True, slots=True)
class ModelPolicyListQuery:
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("Model Policy list limit must be between 1 and 100")
        if self.cursor is not None and not 1 <= len(self.cursor) <= 1024:
            raise ValueError("Model Policy cursor must be between 1 and 1024 characters")


@dataclass(frozen=True, slots=True)
class ModelPolicyCursorPosition:
    created_at: datetime
    policy_id: str
    version: str


@dataclass(frozen=True, slots=True)
class ModelPolicyStoreQuery:
    limit: int
    after: ModelPolicyCursorPosition | None
    summary_started_at: datetime
    summary_ended_at: datetime


@dataclass(frozen=True, slots=True)
class ModelPolicyListItem:
    policy: ModelPolicyVersion
    invocation_summary: ModelInvocationSummary
    impact: ModelPolicyImpact | None


@dataclass(frozen=True, slots=True)
class ModelPolicyListPage:
    items: tuple[ModelPolicyListItem, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ModelPolicyEvaluation:
    eval_run_id: str
    state: Literal["queued", "running", "passed", "failed"]
    impact: ModelPolicyImpact

    def __post_init__(self) -> None:
        if not 16 <= len(self.eval_run_id) <= 64 or self.eval_run_id.strip() != self.eval_run_id:
            raise ValueError("eval_run_id must contain 16-64 non-padding characters")


@dataclass(frozen=True, slots=True)
class ModelPolicyActivation:
    policy: ModelPolicyVersion
    impact_fingerprint: str
    replayed: bool

    def __post_init__(self) -> None:
        _fingerprint(self.impact_fingerprint, field_name="impact_fingerprint")


@dataclass(frozen=True, slots=True)
class ModelPolicyActivationCommand:
    actor_id: str
    policy_id: str
    version: str
    candidate_fingerprint: str
    impact_fingerprint: str
    eval_run_id: str
    idempotency_key: str
    request_fingerprint: str
    recorded_at: datetime
    expires_at: datetime
    expected_baseline_fingerprint: str | None

    def __post_init__(self) -> None:
        _identifier(self.policy_id, field_name="policy_id")
        _identifier(self.version, field_name="version")
        _fingerprint(self.candidate_fingerprint, field_name="candidate_fingerprint")
        _fingerprint(self.impact_fingerprint, field_name="impact_fingerprint")
        _fingerprint(self.request_fingerprint, field_name="request_fingerprint")
        if not 16 <= len(self.eval_run_id) <= 64 or self.eval_run_id.strip() != self.eval_run_id:
            raise ValueError("eval_run_id must contain 16-64 non-padding characters")
        if not 1 <= len(self.idempotency_key) <= 255:
            raise ValueError("idempotency_key must contain 1-255 characters")
        if self.expected_baseline_fingerprint is not None:
            _fingerprint(
                self.expected_baseline_fingerprint,
                field_name="expected_baseline_fingerprint",
            )
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at, field_name="recorded_at"))
        object.__setattr__(self, "expires_at", _utc(self.expires_at, field_name="expires_at"))
        if self.expires_at <= self.recorded_at:
            raise ValueError("activation idempotency expiry must be in the future")
