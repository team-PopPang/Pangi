"""Framework-free, secret-safe contracts for Root orchestration."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from pangi.domain.runs import RunMode

HARD_MAX_TASKS = 5
HARD_MAX_TASK_TIMEOUT_SECONDS = 180
HARD_MAX_RUN_TIMEOUT_SECONDS = 600
HARD_MAX_CONNECTION_HINTS = 20
HARD_MAX_TOOL_HINTS = 50

STABLE_ORCHESTRATION_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$"
_STABLE_IDENTIFIER = re.compile(STABLE_ORCHESTRATION_IDENTIFIER_PATTERN)


class CompositionMode(StrEnum):
    DETERMINISTIC = "deterministic"
    SYNTHESIS_SUBAGENT = "synthesis_subagent"


class EvidenceSourceType(StrEnum):
    MCP = "mcp"
    MEMORY = "memory"
    USER_INPUT = "user_input"
    COMPUTED = "computed"


class AgentResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DelegatedTask:
    id: str
    subagent: str
    objective: str = field(repr=False)
    depends_on: tuple[str, ...] = ()
    connection_hints: tuple[str, ...] = ()
    allowed_tool_hints: tuple[str, ...] = ()
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        _validate_identifier(self.id, field_name="task id")
        _validate_identifier(self.subagent, field_name="subagent")
        _validate_text(self.objective, field_name="task objective", limit=10_000)
        _validate_identifier_tuple(
            self.depends_on,
            field_name="task dependencies",
            maximum=HARD_MAX_TASKS,
        )
        _validate_identifier_tuple(
            self.connection_hints,
            field_name="connection hints",
            maximum=HARD_MAX_CONNECTION_HINTS,
        )
        _validate_identifier_tuple(
            self.allowed_tool_hints,
            field_name="allowed tool hints",
            maximum=HARD_MAX_TOOL_HINTS,
        )
        _validate_integer_range(
            self.timeout_seconds,
            field_name="task timeout_seconds",
            minimum=1,
            maximum=HARD_MAX_TASK_TIMEOUT_SECONDS,
        )


@dataclass(frozen=True, slots=True)
class OrchestratorDecision:
    mode: RunMode
    direct_answer: str | None = field(default=None, repr=False)
    skill_name: str | None = None
    tasks: tuple[DelegatedTask, ...] = ()
    composition: CompositionMode = CompositionMode.DETERMINISTIC
    user_message: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "mode", RunMode(self.mode))
            object.__setattr__(self, "composition", CompositionMode(self.composition))
        except ValueError as error:
            raise ValueError("Orchestrator decision contains an invalid enum value") from error
        if self.direct_answer is not None:
            _validate_text(
                self.direct_answer,
                field_name="direct_answer",
                limit=100_000,
            )
        if self.skill_name is not None:
            _validate_identifier(self.skill_name, field_name="skill_name")
        if not isinstance(self.tasks, tuple):
            raise ValueError("tasks must be an immutable tuple")
        if any(not isinstance(task, DelegatedTask) for task in self.tasks):
            raise TypeError("tasks must contain DelegatedTask values")
        if self.user_message is not None:
            _validate_text(self.user_message, field_name="user_message", limit=2_000)


@dataclass(frozen=True, slots=True)
class Evidence:
    source_type: EvidenceSourceType
    source_name: str
    title: str = field(repr=False)
    uri: str | None = field(default=None, repr=False)
    excerpt: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "source_type", EvidenceSourceType(self.source_type))
        except ValueError as error:
            raise ValueError("Evidence source_type is invalid") from error
        _validate_identifier(self.source_name, field_name="evidence source_name")
        _validate_text(self.title, field_name="evidence title", limit=512)
        if self.uri is not None:
            _validate_text(self.uri, field_name="evidence uri", limit=2_048)
        if self.excerpt is not None:
            _validate_text(self.excerpt, field_name="evidence excerpt", limit=4_000)


@dataclass(frozen=True, slots=True)
class AgentResult:
    task_id: str
    status: AgentResultStatus
    summary_markdown: str = field(repr=False)
    evidence: tuple[Evidence, ...] = ()
    facts: tuple[Mapping[str, object], ...] = field(default=(), repr=False)
    warnings: tuple[str, ...] = field(default=(), repr=False)
    error_code: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.task_id, field_name="result task_id")
        try:
            object.__setattr__(self, "status", AgentResultStatus(self.status))
        except ValueError as error:
            raise ValueError("AgentResult status is invalid") from error
        _validate_text(
            self.summary_markdown,
            field_name="result summary_markdown",
            limit=100_000,
        )
        if not isinstance(self.evidence, tuple):
            raise ValueError("evidence must be an immutable tuple")
        if len(self.evidence) > 100 or any(
            not isinstance(item, Evidence) for item in self.evidence
        ):
            raise ValueError("evidence must contain at most 100 Evidence values")
        if not isinstance(self.facts, tuple):
            raise ValueError("facts must be an immutable tuple")
        if len(self.facts) > 100:
            raise ValueError("facts must contain at most 100 values")
        frozen_facts: list[Mapping[str, object]] = []
        for fact in self.facts:
            if not isinstance(fact, Mapping):
                raise ValueError("facts must contain JSON-compatible objects")
            frozen = _freeze_json_value(fact)
            assert isinstance(frozen, Mapping)
            frozen_facts.append(frozen)
        object.__setattr__(self, "facts", tuple(frozen_facts))
        if not isinstance(self.warnings, tuple) or len(self.warnings) > 100:
            raise ValueError("warnings must be an immutable tuple with at most 100 values")
        for warning in self.warnings:
            _validate_text(warning, field_name="result warning", limit=2_000)
        if self.error_code is not None:
            _validate_identifier(self.error_code, field_name="result error_code")
        if self.status is AgentResultStatus.FAILED and self.error_code is None:
            raise ValueError("a failed AgentResult requires error_code")
        if self.status is AgentResultStatus.SUCCEEDED and self.error_code is not None:
            raise ValueError("a succeeded AgentResult cannot contain error_code")


@dataclass(frozen=True, slots=True)
class OrchestratorCatalog:
    available_subagents: frozenset[str]
    active_skills: frozenset[str]

    def __post_init__(self) -> None:
        _validate_identifier_set(
            self.available_subagents,
            field_name="available_subagents",
        )
        _validate_identifier_set(self.active_skills, field_name="active_skills")


@dataclass(frozen=True, slots=True)
class OrchestratorLimits:
    max_tasks: int = 3
    max_task_timeout_seconds: int = HARD_MAX_TASK_TIMEOUT_SECONDS
    run_timeout_seconds: int = 180

    def __post_init__(self) -> None:
        _validate_integer_range(
            self.max_tasks,
            field_name="max_tasks",
            minimum=1,
            maximum=HARD_MAX_TASKS,
        )
        _validate_integer_range(
            self.max_task_timeout_seconds,
            field_name="max_task_timeout_seconds",
            minimum=1,
            maximum=HARD_MAX_TASK_TIMEOUT_SECONDS,
        )
        _validate_integer_range(
            self.run_timeout_seconds,
            field_name="run_timeout_seconds",
            minimum=1,
            maximum=HARD_MAX_RUN_TIMEOUT_SECONDS,
        )


@dataclass(frozen=True, slots=True)
class ValidatedOrchestratorPlan:
    decision: OrchestratorDecision
    ordered_tasks: tuple[DelegatedTask, ...]
    critical_path_timeout_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.ordered_tasks, tuple):
            raise ValueError("ordered_tasks must be an immutable tuple")
        if any(not isinstance(task, DelegatedTask) for task in self.ordered_tasks):
            raise TypeError("ordered_tasks must contain DelegatedTask values")
        if self.critical_path_timeout_seconds < 0:
            raise ValueError("critical_path_timeout_seconds cannot be negative")


def _validate_identifier(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _STABLE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a stable identifier")


def _validate_identifier_tuple(
    value: object,
    *,
    field_name: str,
    maximum: int,
) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be an immutable tuple")
    if len(value) > maximum:
        raise ValueError(f"{field_name} must contain at most {maximum} values")
    for item in value:
        _validate_identifier(item, field_name=field_name)


def _validate_identifier_set(value: object, *, field_name: str) -> None:
    if not isinstance(value, frozenset):
        raise ValueError(f"{field_name} must be an immutable frozenset")
    for item in value:
        _validate_identifier(item, field_name=field_name)


def _validate_text(value: object, *, field_name: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field_name} must contain 1-{limit} non-blank characters")


def _validate_integer_range(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")


def _freeze_json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > 100_000:
            raise ValueError("fact strings must not exceed 100000 characters")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("facts must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        if len(value) > 100:
            raise ValueError("fact objects must contain at most 100 fields")
        frozen: dict[str, object] = {}
        for key, nested in value.items():
            if not isinstance(key, str) or not key or len(key) > 120:
                raise ValueError("fact keys must be bounded strings")
            frozen[key] = _freeze_json_value(nested)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        if len(value) > 1_000:
            raise ValueError("fact arrays must contain at most 1000 values")
        return tuple(_freeze_json_value(nested) for nested in value)
    raise ValueError("facts must contain only JSON-compatible values")
