"""Secret-safe contracts for deterministic Model routing and execution."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TypeVar

from pangi.application.contracts.policy_impact import PolicyFingerprintReference
from pangi.application.contracts.redaction import RedactionSummary
from pangi.domain.model_routing import (
    DataClass,
    ModelPolicyErrorCode,
    ModelPolicyOutcome,
    ModelPolicyStage,
    ModelProviderErrorCode,
    ModelPurpose,
    ModelRetention,
    data_class_rank,
)

_STABLE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_LOWER_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ModelEnum = TypeVar("_ModelEnum", DataClass, ModelPurpose)


def _stable_identifier(value: str, *, field_name: str) -> None:
    if _STABLE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a stable identifier")


def _lower_identifier(value: str, *, field_name: str) -> None:
    if _LOWER_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a stable lowercase identifier")


def _opaque_ascii(value: str, *, field_name: str, limit: int = 255) -> None:
    if not 1 <= len(value) <= limit or value.strip() != value:
        raise ValueError(f"{field_name} must be a bounded opaque identifier")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in value):
        raise ValueError(f"{field_name} must contain visible ASCII characters")


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _enum_frozenset(
    values: frozenset[_ModelEnum],
    enum_type: type[_ModelEnum],
    *,
    field_name: str,
) -> frozenset[_ModelEnum]:
    if not isinstance(values, frozenset) or not values:
        raise ValueError(f"{field_name} must be a non-empty immutable frozenset")
    try:
        return frozenset(enum_type(value) for value in values)
    except ValueError as error:
        raise ValueError(f"{field_name} contains an invalid value") from error


def _source_kinds(values: frozenset[str], *, field_name: str) -> frozenset[str]:
    if not isinstance(values, frozenset) or not values:
        raise ValueError(f"{field_name} must be a non-empty immutable frozenset")
    if any(_LOWER_IDENTIFIER.fullmatch(value) is None for value in values):
        raise ValueError(f"{field_name} contains an invalid source kind")
    return values


def _opaque_values(values: frozenset[str], *, field_name: str) -> frozenset[str]:
    if not isinstance(values, frozenset) or not values:
        raise ValueError(f"{field_name} must be a non-empty immutable frozenset")
    for value in values:
        _opaque_ascii(value, field_name=field_name)
    return values


@dataclass(frozen=True, slots=True)
class StructuredOutputSchema:
    name: str
    canonical_schema_json: str = field(repr=False)

    def __post_init__(self) -> None:
        _stable_identifier(self.name, field_name="structured output name")
        try:
            schema = json.loads(self.canonical_schema_json)
        except (json.JSONDecodeError, RecursionError) as error:
            raise ValueError("structured output schema must contain valid JSON") from error
        if not isinstance(schema, dict):
            raise ValueError("structured output schema must contain a JSON object")
        canonical = json.dumps(
            schema,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        object.__setattr__(self, "canonical_schema_json", canonical)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_schema_json.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelInputSource:
    source_kind: str
    data_classes: frozenset[DataClass]
    content: str = field(repr=False)
    raw_content: bool
    canonical_data_json: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _lower_identifier(self.source_kind, field_name="source_kind")
        object.__setattr__(
            self,
            "data_classes",
            _enum_frozenset(self.data_classes, DataClass, field_name="data_classes"),
        )
        if not self.content.strip():
            raise ValueError("Model input content cannot be blank")
        if not isinstance(self.raw_content, bool):
            raise ValueError("raw_content must be a boolean")
        if self.canonical_data_json is not None:
            try:
                data = json.loads(self.canonical_data_json)
            except (json.JSONDecodeError, RecursionError) as error:
                raise ValueError("Model source data must contain valid JSON") from error
            if not isinstance(data, dict):
                raise ValueError("Model source data must contain a JSON object")
            canonical = json.dumps(
                data,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            object.__setattr__(self, "canonical_data_json", canonical)


@dataclass(frozen=True, slots=True)
class ModelCallRequest:
    logical_call_id: str = field(repr=False)
    profile: str
    purpose: ModelPurpose
    sources: tuple[ModelInputSource, ...]
    output_schema: StructuredOutputSchema

    def __post_init__(self) -> None:
        _opaque_ascii(self.logical_call_id, field_name="logical_call_id")
        _stable_identifier(self.profile, field_name="profile")
        try:
            object.__setattr__(self, "purpose", ModelPurpose(self.purpose))
        except ValueError as error:
            raise ValueError("purpose is invalid") from error
        if not isinstance(self.sources, tuple) or not self.sources:
            raise ValueError("sources must be a non-empty immutable tuple")
        if any(not isinstance(source, ModelInputSource) for source in self.sources):
            raise TypeError("sources must contain ModelInputSource values")


@dataclass(frozen=True, slots=True)
class ModelProfile:
    profile_id: str
    profile: str
    profile_version: str
    provider: str
    model: str
    region: str | None
    supported_data_classes: frozenset[DataClass]
    supported_source_kinds: frozenset[str]
    supported_purposes: frozenset[ModelPurpose]
    retention: ModelRetention
    allow_raw_content: bool
    routing_priority: int
    active: bool = True

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.profile_id, "profile_id"),
            (self.profile, "profile"),
            (self.profile_version, "profile_version"),
        ):
            _stable_identifier(value, field_name=field_name)
        _lower_identifier(self.provider, field_name="provider")
        _opaque_ascii(self.model, field_name="model")
        if self.region is not None:
            _lower_identifier(self.region, field_name="region")
        object.__setattr__(
            self,
            "supported_data_classes",
            _enum_frozenset(
                self.supported_data_classes,
                DataClass,
                field_name="supported_data_classes",
            ),
        )
        object.__setattr__(
            self,
            "supported_source_kinds",
            _source_kinds(
                self.supported_source_kinds,
                field_name="supported_source_kinds",
            ),
        )
        object.__setattr__(
            self,
            "supported_purposes",
            _enum_frozenset(
                self.supported_purposes,
                ModelPurpose,
                field_name="supported_purposes",
            ),
        )
        try:
            object.__setattr__(self, "retention", ModelRetention(self.retention))
        except ValueError as error:
            raise ValueError("retention is invalid") from error
        if not isinstance(self.allow_raw_content, bool):
            raise ValueError("allow_raw_content must be a boolean")
        if not 0 <= self.routing_priority <= 100_000:
            raise ValueError("routing_priority must be between 0 and 100000")
        if not isinstance(self.active, bool):
            raise ValueError("active must be a boolean")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "active": self.active,
                "allow_raw_content": self.allow_raw_content,
                "model": self.model,
                "profile": self.profile,
                "profile_id": self.profile_id,
                "profile_version": self.profile_version,
                "provider": self.provider,
                "region": self.region,
                "retention": self.retention.value,
                "routing_priority": self.routing_priority,
                "supported_data_classes": sorted(
                    value.value for value in self.supported_data_classes
                ),
                "supported_purposes": sorted(value.value for value in self.supported_purposes),
                "supported_source_kinds": sorted(self.supported_source_kinds),
            }
        )

    def impact_reference(self) -> PolicyFingerprintReference:
        return PolicyFingerprintReference(
            policy_kind="model.profile",
            policy_id=self.profile_id,
            policy_version=self.profile_version,
            policy_fingerprint=self.fingerprint,
        )


@dataclass(frozen=True, slots=True)
class ModelEgressPolicy:
    policy_id: str
    policy_version: str
    profile: str
    allowed_providers: frozenset[str]
    allowed_models: frozenset[str]
    allowed_regions: frozenset[str]
    allowed_data_classes: frozenset[DataClass]
    allowed_source_kinds: frozenset[str]
    allowed_purposes: frozenset[ModelPurpose]
    require_redaction: bool
    require_zero_retention: bool
    allow_raw_content: bool

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.policy_id, "policy_id"),
            (self.policy_version, "policy_version"),
            (self.profile, "profile"),
        ):
            _stable_identifier(value, field_name=field_name)
        if not isinstance(self.allowed_providers, frozenset) or not self.allowed_providers:
            raise ValueError("allowed_providers must be a non-empty immutable frozenset")
        for provider in self.allowed_providers:
            _lower_identifier(provider, field_name="allowed_providers")
        _opaque_values(self.allowed_models, field_name="allowed_models")
        if not isinstance(self.allowed_regions, frozenset):
            raise ValueError("allowed_regions must be an immutable frozenset")
        for region in self.allowed_regions:
            _lower_identifier(region, field_name="allowed_regions")
        object.__setattr__(
            self,
            "allowed_data_classes",
            _enum_frozenset(
                self.allowed_data_classes,
                DataClass,
                field_name="allowed_data_classes",
            ),
        )
        object.__setattr__(
            self,
            "allowed_source_kinds",
            _source_kinds(self.allowed_source_kinds, field_name="allowed_source_kinds"),
        )
        object.__setattr__(
            self,
            "allowed_purposes",
            _enum_frozenset(
                self.allowed_purposes,
                ModelPurpose,
                field_name="allowed_purposes",
            ),
        )
        for boolean_value, boolean_field_name in (
            (self.require_redaction, "require_redaction"),
            (self.require_zero_retention, "require_zero_retention"),
            (self.allow_raw_content, "allow_raw_content"),
        ):
            if not isinstance(boolean_value, bool):
                raise ValueError(f"{boolean_field_name} must be a boolean")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "allow_raw_content": self.allow_raw_content,
                "allowed_data_classes": sorted(value.value for value in self.allowed_data_classes),
                "allowed_models": sorted(self.allowed_models),
                "allowed_providers": sorted(self.allowed_providers),
                "allowed_purposes": sorted(value.value for value in self.allowed_purposes),
                "allowed_regions": sorted(self.allowed_regions),
                "allowed_source_kinds": sorted(self.allowed_source_kinds),
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "profile": self.profile,
                "require_redaction": self.require_redaction,
                "require_zero_retention": self.require_zero_retention,
            }
        )

    def impact_reference(self) -> PolicyFingerprintReference:
        return PolicyFingerprintReference(
            policy_kind="model.egress",
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            policy_fingerprint=self.fingerprint,
        )


@dataclass(frozen=True, slots=True)
class ModelPolicyDecision:
    profile: str
    purpose: ModelPurpose
    stage: ModelPolicyStage
    outcome: ModelPolicyOutcome
    data_classes: tuple[DataClass, ...]
    highest_data_class: DataClass
    source_kinds: tuple[str, ...]
    evaluated_candidate_count: int
    eligible_candidate_count: int
    policy_version: str | None = None
    policy_fingerprint: str | None = None
    selected_profile_id: str | None = None
    selected_profile_fingerprint: str | None = None
    provider: str | None = None
    model: str | None = None
    region: str | None = None
    redaction: RedactionSummary | None = None
    input_fingerprint: str | None = None
    error_code: ModelPolicyErrorCode | None = None

    def __post_init__(self) -> None:
        _stable_identifier(self.profile, field_name="profile")
        try:
            object.__setattr__(self, "purpose", ModelPurpose(self.purpose))
            object.__setattr__(self, "stage", ModelPolicyStage(self.stage))
            object.__setattr__(self, "outcome", ModelPolicyOutcome(self.outcome))
            object.__setattr__(self, "highest_data_class", DataClass(self.highest_data_class))
            if self.error_code is not None:
                object.__setattr__(self, "error_code", ModelPolicyErrorCode(self.error_code))
        except ValueError as error:
            raise ValueError("Model policy decision contains an invalid enum value") from error
        if not isinstance(self.data_classes, tuple) or not self.data_classes:
            raise ValueError("data_classes must be a non-empty immutable tuple")
        normalized_classes = tuple(DataClass(value) for value in self.data_classes)
        expected_classes = tuple(sorted(set(normalized_classes), key=data_class_rank))
        if normalized_classes != expected_classes:
            raise ValueError("data_classes must contain unique values in sensitivity order")
        if self.highest_data_class is not normalized_classes[-1]:
            raise ValueError("highest_data_class must match the most restrictive data class")
        if (
            not isinstance(self.source_kinds, tuple)
            or not self.source_kinds
            or self.source_kinds != tuple(sorted(set(self.source_kinds)))
        ):
            raise ValueError("source_kinds must contain unique sorted values")
        if self.evaluated_candidate_count < 0 or self.eligible_candidate_count < 0:
            raise ValueError("candidate counts cannot be negative")
        if self.eligible_candidate_count > self.evaluated_candidate_count:
            raise ValueError("eligible candidate count cannot exceed evaluated candidates")
        if (self.policy_version is None) is not (self.policy_fingerprint is None):
            raise ValueError("policy version and fingerprint must be present together")
        if self.policy_version is not None:
            _stable_identifier(self.policy_version, field_name="policy_version")
            assert self.policy_fingerprint is not None
            if _SHA256.fullmatch(self.policy_fingerprint) is None:
                raise ValueError("policy_fingerprint must be a SHA-256 hex digest")
        selected = (
            self.selected_profile_id,
            self.selected_profile_fingerprint,
            self.provider,
            self.model,
        )
        if any(value is None for value in selected) is not all(value is None for value in selected):
            raise ValueError("selected Profile metadata must be present together")
        if self.selected_profile_id is not None:
            _stable_identifier(self.selected_profile_id, field_name="selected_profile_id")
            assert self.selected_profile_fingerprint is not None
            if _SHA256.fullmatch(self.selected_profile_fingerprint) is None:
                raise ValueError("selected profile fingerprint must be a SHA-256 hex digest")
            assert self.provider is not None and self.model is not None
            _lower_identifier(self.provider, field_name="provider")
            _opaque_ascii(self.model, field_name="model")
            if self.region is not None:
                _lower_identifier(self.region, field_name="region")
        if self.input_fingerprint is not None and _SHA256.fullmatch(self.input_fingerprint) is None:
            raise ValueError("input_fingerprint must be a SHA-256 hex digest")
        if self.outcome is ModelPolicyOutcome.ALLOWED:
            if self.stage is not ModelPolicyStage.COMPLETE or self.error_code is not None:
                raise ValueError("an allowed Model decision must be complete")
            if any(value is None for value in selected):
                raise ValueError("an allowed Model decision requires a selected Profile")
            if self.redaction is None or self.input_fingerprint is None:
                raise ValueError("an allowed Model decision requires safe input metadata")
            if self.eligible_candidate_count < 1:
                raise ValueError("an allowed Model decision requires an eligible candidate")
        elif self.stage is ModelPolicyStage.COMPLETE or self.error_code is None:
            raise ValueError("a blocked Model decision requires a rejection stage and error code")

    def as_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "purpose": self.purpose.value,
            "stage": self.stage.value,
            "outcome": self.outcome.value,
            "data_classes": [value.value for value in self.data_classes],
            "highest_data_class": self.highest_data_class.value,
            "source_kinds": list(self.source_kinds),
            "evaluated_candidate_count": self.evaluated_candidate_count,
            "eligible_candidate_count": self.eligible_candidate_count,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "selected_profile_id": self.selected_profile_id,
            "selected_profile_fingerprint": self.selected_profile_fingerprint,
            "provider": self.provider,
            "model": self.model,
            "region": self.region,
            "redaction": self.redaction.as_dict() if self.redaction is not None else None,
            "input_fingerprint": self.input_fingerprint,
            "error_code": self.error_code.value if self.error_code is not None else None,
        }


@dataclass(frozen=True, slots=True)
class GuardedModelRequest:
    logical_call_id: str = field(repr=False)
    profile: ModelProfile
    purpose: ModelPurpose
    sources: tuple[ModelInputSource, ...] = field(repr=False)
    output_schema: StructuredOutputSchema = field(repr=False)
    input_fingerprint: str
    decision: ModelPolicyDecision

    def __post_init__(self) -> None:
        _opaque_ascii(self.logical_call_id, field_name="logical_call_id")
        if not isinstance(self.sources, tuple) or not self.sources:
            raise ValueError("guarded sources must be a non-empty immutable tuple")
        try:
            object.__setattr__(self, "purpose", ModelPurpose(self.purpose))
        except ValueError as error:
            raise ValueError("purpose is invalid") from error
        if _SHA256.fullmatch(self.input_fingerprint) is None:
            raise ValueError("input_fingerprint must be a SHA-256 hex digest")
        if self.decision.outcome is not ModelPolicyOutcome.ALLOWED:
            raise ValueError("a guarded Model request requires an allowed decision")
        if self.decision.selected_profile_id != self.profile.profile_id:
            raise ValueError("guarded Model Profile and decision must match")
        if self.decision.input_fingerprint != self.input_fingerprint:
            raise ValueError("guarded Model input and decision fingerprints must match")


@dataclass(frozen=True, slots=True)
class ModelProviderResponse:
    canonical_output_json: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            output = json.loads(self.canonical_output_json)
        except (json.JSONDecodeError, RecursionError) as error:
            raise ValueError("structured Model output must contain valid JSON") from error
        if not isinstance(output, dict):
            raise ValueError("structured Model output must contain a JSON object")
        canonical = json.dumps(
            output,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        object.__setattr__(self, "canonical_output_json", canonical)

    @property
    def output_fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_output_json.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class GuardedModelExecution:
    response: ModelProviderResponse = field(repr=False)
    decision: ModelPolicyDecision

    def __post_init__(self) -> None:
        if self.decision.outcome is not ModelPolicyOutcome.ALLOWED:
            raise ValueError("a guarded Model execution requires an allowed decision")


class ModelPolicyBlockedError(RuntimeError):
    def __init__(self, decision: ModelPolicyDecision) -> None:
        if decision.error_code is None:
            raise ValueError("a blocked Model decision requires an error code")
        super().__init__(f"Model policy blocked call: {decision.error_code.value}")
        self.decision = decision

    @property
    def code(self) -> ModelPolicyErrorCode:
        assert self.decision.error_code is not None
        return self.decision.error_code


class ModelProviderFailure(RuntimeError):
    def __init__(self, code: ModelProviderErrorCode, *, retryable: bool) -> None:
        try:
            normalized = ModelProviderErrorCode(code)
        except ValueError:
            raise ValueError("Model Provider error code is invalid") from None
        if not isinstance(retryable, bool):
            raise ValueError("retryable must be a boolean")
        super().__init__(f"Model Provider failed: {normalized.value}")
        self.code = normalized
        self.retryable = retryable
