"""Secret-safe contracts for durable orchestration execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from pangi.application.contracts.orchestration import (
    HARD_MAX_TASKS,
    AgentResult,
    AgentResultStatus,
    CompositionMode,
    DelegatedTask,
    Evidence,
    EvidenceSourceType,
    ValidatedOrchestratorPlan,
)
from pangi.domain.runs import (
    RunErrorCode,
    RunMode,
    RunState,
    RunStep,
    StepRequirement,
    StepState,
)

EXECUTION_PLAN_SCHEMA_VERSION = "orchestration-execution-v1"
EXECUTION_RESULT_SCHEMA_VERSION = "agent-result-v1"


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    max_parallel_steps: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_parallel_steps, int)
            or isinstance(self.max_parallel_steps, bool)
            or not 1 <= self.max_parallel_steps <= HARD_MAX_TASKS
        ):
            raise ValueError(f"max_parallel_steps must be between 1 and {HARD_MAX_TASKS}")


@dataclass(frozen=True, slots=True)
class PreparedExecutionStep:
    task: DelegatedTask = field(repr=False)
    requirement: StepRequirement = StepRequirement.REQUIRED
    idempotent: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.task, DelegatedTask):
            raise TypeError("task must be DelegatedTask")
        try:
            object.__setattr__(self, "requirement", StepRequirement(self.requirement))
        except ValueError as error:
            raise ValueError("execution step requirement is invalid") from error
        if not isinstance(self.idempotent, bool):
            raise ValueError("execution step idempotent must be a boolean")


@dataclass(frozen=True, slots=True)
class PreparedExecutionPlan:
    mode: RunMode
    steps: tuple[PreparedExecutionStep, ...] = ()
    direct_answer: str | None = field(default=None, repr=False)
    composition: CompositionMode = CompositionMode.DETERMINISTIC

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "mode", RunMode(self.mode))
            object.__setattr__(self, "composition", CompositionMode(self.composition))
        except ValueError as error:
            raise ValueError("execution plan contains an invalid enum value") from error
        if not isinstance(self.steps, tuple) or any(
            not isinstance(step, PreparedExecutionStep) for step in self.steps
        ):
            raise TypeError("execution plan steps must be an immutable prepared tuple")
        if len(self.steps) > HARD_MAX_TASKS:
            raise ValueError(f"execution plan can contain at most {HARD_MAX_TASKS} steps")
        if self.mode is RunMode.DIRECT:
            if self.direct_answer is None or not self.direct_answer.strip() or self.steps:
                raise ValueError("a direct execution plan requires only direct_answer")
            if self.composition is not CompositionMode.DETERMINISTIC:
                raise ValueError("a direct execution plan requires deterministic composition")
            return
        if self.mode is not RunMode.DELEGATE:
            raise ValueError("skill execution is owned by the Skill runtime")
        if self.direct_answer is not None or not self.steps:
            raise ValueError("a delegate execution plan requires steps only")
        identifiers = tuple(step.task.id for step in self.steps)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("execution step identifiers must be unique")
        seen: set[str] = set()
        for step in self.steps:
            if any(dependency not in seen for dependency in step.task.depends_on):
                raise ValueError("execution steps must be in validated dependency order")
            seen.add(step.task.id)

    @classmethod
    def from_validated(cls, plan: ValidatedOrchestratorPlan) -> PreparedExecutionPlan:
        if not isinstance(plan, ValidatedOrchestratorPlan):
            raise TypeError("plan must be ValidatedOrchestratorPlan")
        decision = plan.decision
        if decision.mode is RunMode.DIRECT:
            return cls(
                mode=RunMode.DIRECT,
                direct_answer=decision.direct_answer,
                composition=decision.composition,
            )
        if decision.mode is RunMode.SKILL:
            raise ValueError("skill execution is owned by the Skill runtime")
        return cls(
            mode=RunMode.DELEGATE,
            steps=tuple(PreparedExecutionStep(task) for task in plan.ordered_tasks),
            composition=decision.composition,
        )

    @property
    def fingerprint(self) -> str:
        return _fingerprint(execution_plan_data(self))


@dataclass(frozen=True, slots=True)
class ExecutionStepSnapshot:
    step: RunStep
    definition: PreparedExecutionStep = field(repr=False)
    result: AgentResult | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.step, RunStep):
            raise TypeError("step must be RunStep")
        if not isinstance(self.definition, PreparedExecutionStep):
            raise TypeError("definition must be PreparedExecutionStep")
        if self.step.node_id != self.definition.task.id:
            raise ValueError("persisted Step and execution definition do not match")
        if self.step.requirement is not self.definition.requirement:
            raise ValueError("persisted Step requirement does not match its definition")
        if self.step.idempotent is not self.definition.idempotent:
            raise ValueError("persisted Step idempotency does not match its definition")
        if self.result is not None and self.result.task_id != self.step.node_id:
            raise ValueError("persisted AgentResult belongs to another Step")
        result_states = {StepState.COMPLETED, StepState.FAILED}
        if self.step.state in result_states and self.result is None:
            raise ValueError("a completed or failed execution Step requires an AgentResult")
        if self.result is not None and self.step.state not in result_states:
            raise ValueError("only completed or failed execution Steps can contain an AgentResult")
        if self.result is not None:
            result_failed = self.result.status is AgentResultStatus.FAILED
            if result_failed is not (self.step.state is StepState.FAILED):
                raise ValueError("execution Step state and AgentResult status do not match")


@dataclass(frozen=True, slots=True)
class ExecutionPlanSnapshot:
    plan: PreparedExecutionPlan = field(repr=False)
    plan_fingerprint: str
    steps: tuple[ExecutionStepSnapshot, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.plan, PreparedExecutionPlan):
            raise TypeError("plan must be PreparedExecutionPlan")
        if not _is_fingerprint(self.plan_fingerprint):
            raise ValueError("plan_fingerprint must be a SHA-256 hex digest")
        if not isinstance(self.steps, tuple) or any(
            not isinstance(step, ExecutionStepSnapshot) for step in self.steps
        ):
            raise TypeError("steps must be an immutable ExecutionStepSnapshot tuple")
        if self.plan.mode is RunMode.DIRECT and self.steps:
            raise ValueError("a direct execution snapshot cannot contain Steps")


@dataclass(frozen=True, slots=True)
class StepExecutionRequest:
    run_id: str
    step_id: str
    task: DelegatedTask = field(repr=False)
    dependency_results: tuple[AgentResult, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _bounded_identifier(self.run_id, field_name="run_id")
        _bounded_identifier(self.step_id, field_name="step_id")
        if not isinstance(self.task, DelegatedTask):
            raise TypeError("task must be DelegatedTask")
        if not isinstance(self.dependency_results, tuple) or any(
            not isinstance(result, AgentResult) for result in self.dependency_results
        ):
            raise TypeError("dependency_results must be an immutable AgentResult tuple")
        if tuple(result.task_id for result in self.dependency_results) != self.task.depends_on:
            raise ValueError("dependency_results must follow the declared dependency order")


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    run_id: str
    state: RunState
    results: tuple[AgentResult, ...] = field(default=(), repr=False)
    direct_answer: str | None = field(default=None, repr=False)
    warnings: tuple[str, ...] = field(default=(), repr=False)
    error_code: RunErrorCode | None = None

    def __post_init__(self) -> None:
        _bounded_identifier(self.run_id, field_name="run_id")
        try:
            object.__setattr__(self, "state", RunState(self.state))
            if self.error_code is not None:
                object.__setattr__(self, "error_code", RunErrorCode(self.error_code))
        except ValueError as error:
            raise ValueError("execution outcome contains an invalid enum value") from error
        if self.state not in {RunState.COMPOSING, RunState.FAILED}:
            raise ValueError("execution outcome must be composing or failed")
        if not isinstance(self.results, tuple) or any(
            not isinstance(result, AgentResult) for result in self.results
        ):
            raise TypeError("results must be an immutable AgentResult tuple")
        if not isinstance(self.warnings, tuple) or any(
            not isinstance(warning, str) or not warning.strip() for warning in self.warnings
        ):
            raise ValueError("warnings must be an immutable non-blank string tuple")
        if self.direct_answer is not None and self.results:
            raise ValueError("a direct outcome cannot contain AgentResult values")
        if self.state is RunState.FAILED:
            if self.error_code is not RunErrorCode.REQUIRED_STEP_FAILED:
                raise ValueError("a failed execution requires required_step_failed")
            if self.direct_answer is not None:
                raise ValueError("a failed execution cannot contain direct_answer")
        elif self.error_code not in {None, RunErrorCode.OPTIONAL_STEP_FAILED}:
            raise ValueError("a composing execution has an invalid error_code")


def execution_plan_data(plan: PreparedExecutionPlan) -> dict[str, object]:
    return {
        "composition": plan.composition.value,
        "direct_answer": plan.direct_answer,
        "mode": plan.mode.value,
        "schema_version": EXECUTION_PLAN_SCHEMA_VERSION,
        "steps": [prepared_execution_step_data(step) for step in plan.steps],
    }


def prepared_execution_plan_from_data(value: object) -> PreparedExecutionPlan:
    data = _mapping(value, field_name="execution plan")
    _exact_keys(
        data,
        {"composition", "direct_answer", "mode", "schema_version", "steps"},
        field_name="execution plan",
    )
    if data["schema_version"] != EXECUTION_PLAN_SCHEMA_VERSION:
        raise ValueError("execution plan schema version is unsupported")
    steps_value = data["steps"]
    if not isinstance(steps_value, list):
        raise ValueError("execution plan steps must be an array")
    direct_answer = data["direct_answer"]
    if direct_answer is not None and not isinstance(direct_answer, str):
        raise ValueError("execution plan direct_answer is invalid")
    return PreparedExecutionPlan(
        mode=RunMode(_string(data, "mode")),
        direct_answer=direct_answer,
        composition=CompositionMode(_string(data, "composition")),
        steps=tuple(prepared_execution_step_from_data(item) for item in steps_value),
    )


def prepared_execution_step_data(step: PreparedExecutionStep) -> dict[str, object]:
    return {
        "idempotent": step.idempotent,
        "requirement": step.requirement.value,
        "task": {
            "allowed_tool_hints": list(step.task.allowed_tool_hints),
            "connection_hints": list(step.task.connection_hints),
            "depends_on": list(step.task.depends_on),
            "id": step.task.id,
            "objective": step.task.objective,
            "subagent": step.task.subagent,
            "timeout_seconds": step.task.timeout_seconds,
        },
    }


def agent_result_data(result: AgentResult) -> dict[str, object]:
    return {
        "error_code": result.error_code,
        "evidence": [
            {
                "excerpt": evidence.excerpt,
                "source_name": evidence.source_name,
                "source_type": evidence.source_type.value,
                "title": evidence.title,
                "uri": evidence.uri,
            }
            for evidence in result.evidence
        ],
        "facts": [_json_value(fact) for fact in result.facts],
        "schema_version": EXECUTION_RESULT_SCHEMA_VERSION,
        "status": result.status.value,
        "summary_markdown": result.summary_markdown,
        "task_id": result.task_id,
        "warnings": list(result.warnings),
    }


def agent_result_from_data(value: object) -> AgentResult:
    data = _mapping(value, field_name="AgentResult")
    _exact_keys(
        data,
        {
            "error_code",
            "evidence",
            "facts",
            "schema_version",
            "status",
            "summary_markdown",
            "task_id",
            "warnings",
        },
        field_name="AgentResult",
    )
    if data["schema_version"] != EXECUTION_RESULT_SCHEMA_VERSION:
        raise ValueError("AgentResult schema version is unsupported")
    evidence_value = data["evidence"]
    facts_value = data["facts"]
    warnings_value = data["warnings"]
    error_code = data["error_code"]
    if not isinstance(evidence_value, list):
        raise ValueError("AgentResult evidence must be an array")
    if not isinstance(facts_value, list) or any(not isinstance(item, dict) for item in facts_value):
        raise ValueError("AgentResult facts must be an object array")
    if not isinstance(warnings_value, list) or any(
        not isinstance(item, str) for item in warnings_value
    ):
        raise ValueError("AgentResult warnings must be a string array")
    if error_code is not None and not isinstance(error_code, str):
        raise ValueError("AgentResult error_code is invalid")
    return AgentResult(
        task_id=_string(data, "task_id"),
        status=AgentResultStatus(_string(data, "status")),
        summary_markdown=_string(data, "summary_markdown"),
        evidence=tuple(_evidence(item) for item in evidence_value),
        facts=tuple(facts_value),
        warnings=tuple(warnings_value),
        error_code=error_code,
    )


def canonical_execution_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def prepared_execution_step_from_data(value: object) -> PreparedExecutionStep:
    data = _mapping(value, field_name="execution step")
    _exact_keys(data, {"idempotent", "requirement", "task"}, field_name="execution step")
    task = _mapping(data["task"], field_name="delegated task")
    _exact_keys(
        task,
        {
            "allowed_tool_hints",
            "connection_hints",
            "depends_on",
            "id",
            "objective",
            "subagent",
            "timeout_seconds",
        },
        field_name="delegated task",
    )
    idempotent = data["idempotent"]
    timeout_seconds = task["timeout_seconds"]
    if not isinstance(idempotent, bool):
        raise ValueError("execution step idempotent is invalid")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
        raise ValueError("delegated task timeout_seconds is invalid")
    return PreparedExecutionStep(
        task=DelegatedTask(
            id=_string(task, "id"),
            subagent=_string(task, "subagent"),
            objective=_string(task, "objective"),
            depends_on=_string_tuple(task, "depends_on"),
            connection_hints=_string_tuple(task, "connection_hints"),
            allowed_tool_hints=_string_tuple(task, "allowed_tool_hints"),
            timeout_seconds=timeout_seconds,
        ),
        requirement=StepRequirement(_string(data, "requirement")),
        idempotent=idempotent,
    )


def _evidence(value: object) -> Evidence:
    data = _mapping(value, field_name="Evidence")
    _exact_keys(
        data,
        {"excerpt", "source_name", "source_type", "title", "uri"},
        field_name="Evidence",
    )
    uri = data["uri"]
    excerpt = data["excerpt"]
    if uri is not None and not isinstance(uri, str):
        raise ValueError("Evidence uri is invalid")
    if excerpt is not None and not isinstance(excerpt, str):
        raise ValueError("Evidence excerpt is invalid")
    return Evidence(
        source_type=EvidenceSourceType(_string(data, "source_type")),
        source_name=_string(data, "source_name"),
        title=_string(data, "title"),
        uri=uri,
        excerpt=excerpt,
    )


def _mapping(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} must be an object")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], *, field_name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{field_name} shape is invalid")


def _string(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string")
    return item


def _string_tuple(value: Mapping[str, object], key: str) -> tuple[str, ...]:
    item = value[key]
    if not isinstance(item, list) or any(not isinstance(nested, str) for nested in item):
        raise ValueError(f"{key} must be a string array")
    return tuple(item)


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_json_value(nested) for nested in value]
    return value


def _fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_execution_json(value).encode()).hexdigest()


def _is_fingerprint(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _bounded_identifier(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not 16 <= len(value) <= 64 or value.strip() != value:
        raise ValueError(f"{field_name} must contain 16-64 non-padding characters")
