"""Secret-safe Model Policy snapshots and Invocation persistence contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from pangi.application.contracts.model_routing import (
    ModelEgressPolicy,
    ModelPolicyDecision,
    ModelProfile,
    ModelProviderFailure,
    ModelProviderResponse,
    ModelTokenUsage,
)
from pangi.domain.model_routing import (
    DataClass,
    ModelFinishReason,
    ModelInvocationState,
    ModelPolicyOutcome,
    ModelPurpose,
    ModelRetention,
)

MODEL_POLICY_RULES_SCHEMA_VERSION = 1

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_Required = TypeVar("_Required")


def _run_identifier(value: str, *, field_name: str) -> None:
    if not 16 <= len(value) <= 64 or value.strip() != value:
        raise ValueError(f"{field_name} must contain 16-64 non-padding characters")


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _fingerprint(value: str, *, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def logical_call_fingerprint(logical_call_id: str) -> str:
    """Return a safe stable identity without retaining the caller's opaque value."""

    return hashlib.sha256(logical_call_id.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelPolicySnapshot:
    """One canonical Egress Policy and its ordered Provider candidates."""

    policy: ModelEgressPolicy
    profiles: tuple[ModelProfile, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policy, ModelEgressPolicy):
            raise TypeError("policy must be a ModelEgressPolicy")
        if not isinstance(self.profiles, tuple) or not self.profiles:
            raise ValueError("profiles must be a non-empty immutable tuple")
        if any(not isinstance(profile, ModelProfile) for profile in self.profiles):
            raise TypeError("profiles must contain ModelProfile values")
        ordered = tuple(
            sorted(
                self.profiles,
                key=lambda profile: (profile.routing_priority, profile.profile_id),
            )
        )
        if any(profile.profile != self.policy.profile for profile in ordered):
            raise ValueError("all Model Profiles must belong to the Policy profile")
        identifiers = tuple(profile.profile_id for profile in ordered)
        priorities = tuple(profile.routing_priority for profile in ordered)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Model Policy snapshot contains duplicate Profile identifiers")
        if len(priorities) != len(set(priorities)):
            raise ValueError("Model Policy snapshot contains duplicate routing priorities")
        object.__setattr__(self, "profiles", ordered)

    @property
    def rules_json(self) -> str:
        return _canonical_json(
            {
                "policy": self.policy.as_dict(),
                "profiles": [profile.as_dict() for profile in self.profiles],
                "schema_version": MODEL_POLICY_RULES_SCHEMA_VERSION,
            }
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.rules_json.encode()).hexdigest()

    @classmethod
    def from_rules_json(cls, rules_json: str) -> ModelPolicySnapshot:
        try:
            value = json.loads(rules_json)
        except (json.JSONDecodeError, RecursionError) as error:
            raise ValueError("Model Policy rules must contain valid JSON") from error
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("Model Policy rules use an unsupported schema")
        policy_value = value.get("policy")
        profiles_value = value.get("profiles")
        if not isinstance(policy_value, dict) or not isinstance(profiles_value, list):
            raise ValueError("Model Policy rules have an invalid shape")
        return cls(
            policy=_policy_from_dict(policy_value),
            profiles=tuple(_profile_from_dict(item) for item in profiles_value),
        )


@dataclass(frozen=True, slots=True)
class ModelInvocationContext:
    """Run ownership required before any governed Provider call can begin."""

    run_id: str
    step_id: str | None = None

    def __post_init__(self) -> None:
        _run_identifier(self.run_id, field_name="run_id")
        if self.step_id is not None:
            _run_identifier(self.step_id, field_name="step_id")


@dataclass(frozen=True, slots=True)
class ModelInvocationStart:
    """Safe metadata committed before an allowed Provider call."""

    invocation_id: str
    context: ModelInvocationContext
    logical_call_fingerprint: str
    decision: ModelPolicyDecision
    started_at: datetime

    def __post_init__(self) -> None:
        _run_identifier(self.invocation_id, field_name="invocation_id")
        if not isinstance(self.context, ModelInvocationContext):
            raise TypeError("context must be a ModelInvocationContext")
        _fingerprint(
            self.logical_call_fingerprint,
            field_name="logical_call_fingerprint",
        )
        if self.decision.outcome is not ModelPolicyOutcome.ALLOWED:
            raise ValueError("a running Model Invocation requires an allowed decision")
        object.__setattr__(
            self,
            "started_at",
            _utc(self.started_at, field_name="started_at"),
        )


@dataclass(frozen=True, slots=True)
class ModelInvocationDenial:
    """Safe metadata for a logical call stopped before Provider execution."""

    invocation_id: str
    context: ModelInvocationContext
    logical_call_fingerprint: str
    decision: ModelPolicyDecision
    denied_at: datetime

    def __post_init__(self) -> None:
        _run_identifier(self.invocation_id, field_name="invocation_id")
        if not isinstance(self.context, ModelInvocationContext):
            raise TypeError("context must be a ModelInvocationContext")
        _fingerprint(
            self.logical_call_fingerprint,
            field_name="logical_call_fingerprint",
        )
        if self.decision.outcome is not ModelPolicyOutcome.BLOCKED:
            raise ValueError("a denied Model Invocation requires a blocked decision")
        object.__setattr__(
            self,
            "denied_at",
            _utc(self.denied_at, field_name="denied_at"),
        )


@dataclass(frozen=True, slots=True)
class ModelInvocationFinish:
    """Terminal safe usage metadata for an allowed logical Model call."""

    invocation_id: str
    state: ModelInvocationState
    provider_request_count: int
    duration_ms: int
    finished_at: datetime
    token_usage: ModelTokenUsage | None = None
    provider_latency_ms: int | None = None
    finish_reason: ModelFinishReason | None = None
    output_fingerprint: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        _run_identifier(self.invocation_id, field_name="invocation_id")
        try:
            object.__setattr__(self, "state", ModelInvocationState(self.state))
        except ValueError as error:
            raise ValueError("Model Invocation state is invalid") from error
        if self.state not in {
            ModelInvocationState.COMPLETED,
            ModelInvocationState.FAILED,
        }:
            raise ValueError("Model Invocation finish must be completed or failed")
        if not 1 <= self.provider_request_count <= 10:
            raise ValueError("provider_request_count must be between 1 and 10")
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        if self.token_usage is not None and not isinstance(self.token_usage, ModelTokenUsage):
            raise TypeError("token_usage must be ModelTokenUsage or None")
        if self.provider_latency_ms is not None and self.provider_latency_ms < 0:
            raise ValueError("provider_latency_ms must be non-negative or None")
        if self.finish_reason is not None:
            try:
                object.__setattr__(
                    self,
                    "finish_reason",
                    ModelFinishReason(self.finish_reason),
                )
            except ValueError as error:
                raise ValueError("finish_reason is invalid") from error
        if self.output_fingerprint is not None:
            _fingerprint(self.output_fingerprint, field_name="output_fingerprint")
        if self.state is ModelInvocationState.COMPLETED:
            if self.error_code is not None or self.output_fingerprint is None:
                raise ValueError("a completed Model Invocation requires output metadata")
        elif self.error_code is None:
            raise ValueError("a failed Model Invocation requires an error code")
        object.__setattr__(
            self,
            "finished_at",
            _utc(self.finished_at, field_name="finished_at"),
        )

    @classmethod
    def completed(
        cls,
        invocation_id: str,
        response: ModelProviderResponse,
        *,
        finished_at: datetime,
    ) -> ModelInvocationFinish:
        return cls(
            invocation_id=invocation_id,
            state=ModelInvocationState.COMPLETED,
            provider_request_count=response.provider_request_count,
            duration_ms=response.duration_ms,
            token_usage=response.token_usage,
            provider_latency_ms=response.provider_latency_ms,
            finish_reason=response.finish_reason,
            output_fingerprint=response.output_fingerprint,
            error_code=None,
            finished_at=finished_at,
        )

    @classmethod
    def failed(
        cls,
        invocation_id: str,
        failure: ModelProviderFailure,
        *,
        finished_at: datetime,
    ) -> ModelInvocationFinish:
        return cls(
            invocation_id=invocation_id,
            state=ModelInvocationState.FAILED,
            provider_request_count=failure.provider_request_count,
            duration_ms=failure.duration_ms,
            token_usage=failure.token_usage,
            provider_latency_ms=failure.provider_latency_ms,
            finish_reason=failure.finish_reason,
            output_fingerprint=failure.output_fingerprint,
            error_code=failure.code.value,
            finished_at=finished_at,
        )


def _strings(value: object, *, field_name: str) -> frozenset[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must contain strings")
    return frozenset(value)


def _data_classes(value: object, *, field_name: str) -> frozenset[DataClass]:
    return frozenset(DataClass(item) for item in _strings(value, field_name=field_name))


def _purposes(value: object, *, field_name: str) -> frozenset[ModelPurpose]:
    return frozenset(ModelPurpose(item) for item in _strings(value, field_name=field_name))


def _required(
    value: dict[str, object],
    key: str,
    expected: type[_Required],
) -> _Required:
    item = value.get(key)
    if not isinstance(item, expected):
        raise ValueError(f"Model Policy rules contain an invalid {key}")
    return item


def _optional_string(value: dict[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is not None and not isinstance(item, str):
        raise ValueError(f"Model Policy rules contain an invalid {key}")
    return item


def _profile_from_dict(value: object) -> ModelProfile:
    if not isinstance(value, dict):
        raise ValueError("Model Policy Profile rules must be objects")
    return ModelProfile(
        profile_id=str(_required(value, "profile_id", str)),
        profile=str(_required(value, "profile", str)),
        profile_version=str(_required(value, "profile_version", str)),
        provider=str(_required(value, "provider", str)),
        model=str(_required(value, "model", str)),
        region=_optional_string(value, "region"),
        supported_data_classes=_data_classes(
            value.get("supported_data_classes"),
            field_name="supported_data_classes",
        ),
        supported_source_kinds=_strings(
            value.get("supported_source_kinds"),
            field_name="supported_source_kinds",
        ),
        supported_purposes=_purposes(
            value.get("supported_purposes"),
            field_name="supported_purposes",
        ),
        retention=ModelRetention(str(_required(value, "retention", str))),
        allow_raw_content=bool(_required(value, "allow_raw_content", bool)),
        routing_priority=int(_required(value, "routing_priority", int)),
        active=bool(_required(value, "active", bool)),
    )


def _policy_from_dict(value: dict[str, object]) -> ModelEgressPolicy:
    return ModelEgressPolicy(
        policy_id=str(_required(value, "policy_id", str)),
        policy_version=str(_required(value, "policy_version", str)),
        profile=str(_required(value, "profile", str)),
        allowed_providers=_strings(
            value.get("allowed_providers"),
            field_name="allowed_providers",
        ),
        allowed_models=_strings(
            value.get("allowed_models"),
            field_name="allowed_models",
        ),
        allowed_regions=_strings(
            value.get("allowed_regions"),
            field_name="allowed_regions",
        ),
        allowed_data_classes=_data_classes(
            value.get("allowed_data_classes"),
            field_name="allowed_data_classes",
        ),
        allowed_source_kinds=_strings(
            value.get("allowed_source_kinds"),
            field_name="allowed_source_kinds",
        ),
        allowed_purposes=_purposes(
            value.get("allowed_purposes"),
            field_name="allowed_purposes",
        ),
        require_redaction=bool(_required(value, "require_redaction", bool)),
        require_zero_retention=bool(_required(value, "require_zero_retention", bool)),
        allow_raw_content=bool(_required(value, "allow_raw_content", bool)),
    )
