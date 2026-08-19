"""Secret-safe contracts for one Root orchestration decision."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from pangi.application.contracts.guardrails import GuardedRunRequest
from pangi.application.contracts.orchestration import (
    OrchestratorCatalog,
    OrchestratorLimits,
    ValidatedOrchestratorPlan,
)
from pangi.domain.guardrails import GuardrailOutcome
from pangi.domain.model_routing import DataClass

_STABLE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_MAX_CATALOG_ENTRIES_PER_KIND = 100
_MAX_CATALOG_BYTES = 100_000


@dataclass(frozen=True, slots=True)
class RootSubagentDescriptor:
    name: str
    description: str = field(repr=False)

    def __post_init__(self) -> None:
        _validate_identifier(self.name, field_name="subagent name")
        _validate_text(self.description, field_name="subagent description", limit=1_000)


@dataclass(frozen=True, slots=True)
class RootSkillDescriptor:
    name: str
    description: str = field(repr=False)
    triggers: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _validate_identifier(self.name, field_name="skill name")
        _validate_text(self.description, field_name="skill description", limit=1_000)
        if not isinstance(self.triggers, tuple) or len(self.triggers) > 20:
            raise ValueError("skill triggers must be an immutable tuple with at most 20 values")
        for trigger in self.triggers:
            _validate_text(trigger, field_name="skill trigger", limit=500)
        if len(set(self.triggers)) != len(self.triggers):
            raise ValueError("skill triggers must be unique")
        object.__setattr__(self, "triggers", tuple(sorted(self.triggers)))


@dataclass(frozen=True, slots=True)
class RootCatalogSnapshot:
    version: str
    subagents: tuple[RootSubagentDescriptor, ...] = ()
    skills: tuple[RootSkillDescriptor, ...] = ()
    connection_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(self.version, field_name="catalog version")
        if not isinstance(self.subagents, tuple) or any(
            not isinstance(item, RootSubagentDescriptor) for item in self.subagents
        ):
            raise ValueError("subagents must be an immutable descriptor tuple")
        if not isinstance(self.skills, tuple) or any(
            not isinstance(item, RootSkillDescriptor) for item in self.skills
        ):
            raise ValueError("skills must be an immutable descriptor tuple")
        if not isinstance(self.connection_names, tuple):
            raise ValueError("connection_names must be an immutable tuple")
        for values, field_name in (
            (self.subagents, "subagents"),
            (self.skills, "skills"),
            (self.connection_names, "connection_names"),
        ):
            if len(values) > _MAX_CATALOG_ENTRIES_PER_KIND:
                raise ValueError(
                    f"{field_name} must contain at most {_MAX_CATALOG_ENTRIES_PER_KIND} values"
                )
        for name in self.connection_names:
            _validate_identifier(name, field_name="connection name")
        _require_unique_names(
            tuple(item.name for item in self.subagents),
            field_name="subagent names",
        )
        _require_unique_names(
            tuple(item.name for item in self.skills),
            field_name="skill names",
        )
        _require_unique_names(self.connection_names, field_name="connection names")
        object.__setattr__(
            self,
            "subagents",
            tuple(sorted(self.subagents, key=lambda item: item.name)),
        )
        object.__setattr__(
            self,
            "skills",
            tuple(sorted(self.skills, key=lambda item: item.name)),
        )
        object.__setattr__(self, "connection_names", tuple(sorted(self.connection_names)))
        if len(_canonical_json(self.as_prompt_data()).encode()) > _MAX_CATALOG_BYTES:
            raise ValueError("Root Catalog canonical data exceeds 100000 bytes")

    @property
    def validation_catalog(self) -> OrchestratorCatalog:
        return OrchestratorCatalog(
            available_subagents=frozenset(item.name for item in self.subagents),
            active_skills=frozenset(item.name for item in self.skills),
        )

    @property
    def fingerprint(self) -> str:
        encoded = _canonical_json(self.as_prompt_data()).encode()
        return hashlib.sha256(encoded).hexdigest()

    def as_prompt_data(self) -> dict[str, object]:
        return {
            "connection_names": list(self.connection_names),
            "skills": [
                {
                    "description": item.description,
                    "name": item.name,
                    "triggers": list(item.triggers),
                }
                for item in self.skills
            ],
            "subagents": [
                {
                    "description": item.description,
                    "input_schema_ref": "orchestrator-decision-v1#/$defs/delegated_task",
                    "name": item.name,
                }
                for item in self.subagents
            ],
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class RootOrchestratorPolicy:
    profile: str
    prompt_version: str
    limits: OrchestratorLimits = OrchestratorLimits()

    def __post_init__(self) -> None:
        _validate_identifier(self.profile, field_name="root profile")
        _validate_identifier(self.prompt_version, field_name="root prompt_version")
        if not isinstance(self.limits, OrchestratorLimits):
            raise TypeError("limits must be OrchestratorLimits")


@dataclass(frozen=True, slots=True)
class RootOrchestrationRequest:
    run_id: str
    guarded_request: GuardedRunRequest = field(repr=False)
    data_classes: frozenset[DataClass]

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not 16 <= len(self.run_id) <= 64:
            raise ValueError("run_id must contain 16-64 characters")
        if self.run_id.strip() != self.run_id:
            raise ValueError("run_id cannot contain surrounding whitespace")
        if not isinstance(self.guarded_request, GuardedRunRequest):
            raise TypeError("guarded_request must be GuardedRunRequest")
        if self.guarded_request.decision.outcome is not GuardrailOutcome.ALLOWED:
            raise ValueError("Root orchestration requires an allowed input decision")
        if not isinstance(self.data_classes, frozenset) or not self.data_classes:
            raise ValueError("data_classes must be a non-empty immutable frozenset")
        try:
            normalized = frozenset(DataClass(value) for value in self.data_classes)
        except ValueError as error:
            raise ValueError("data_classes contains an invalid value") from error
        object.__setattr__(self, "data_classes", normalized)


@dataclass(frozen=True, slots=True)
class RootOrchestrationResult:
    plan: ValidatedOrchestratorPlan
    logical_call_count: int
    provider_request_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.plan, ValidatedOrchestratorPlan):
            raise TypeError("plan must be ValidatedOrchestratorPlan")
        if isinstance(self.logical_call_count, bool) or self.logical_call_count not in {0, 1}:
            raise ValueError("logical_call_count must be 0 or 1")
        if not isinstance(self.provider_request_count, int) or isinstance(
            self.provider_request_count, bool
        ):
            raise ValueError("provider_request_count must be an integer")
        if self.logical_call_count == 0 and self.provider_request_count != 0:
            raise ValueError("a zero-call result cannot contain Provider requests")
        if self.logical_call_count == 1 and not 1 <= self.provider_request_count <= 10:
            raise ValueError("a Model result requires 1-10 Provider requests")


def _validate_identifier(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _STABLE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a stable identifier")


def _validate_text(value: object, *, field_name: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field_name} must contain 1-{limit} non-blank characters")


def _require_unique_names(values: tuple[str, ...], *, field_name: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be unique")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
