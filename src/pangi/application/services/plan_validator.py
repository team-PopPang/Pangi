"""Fail-closed validation for Root orchestration decisions."""

from __future__ import annotations

from enum import StrEnum

from pangi.application.contracts.orchestration import (
    CompositionMode,
    DelegatedTask,
    OrchestratorCatalog,
    OrchestratorDecision,
    OrchestratorLimits,
    ValidatedOrchestratorPlan,
)
from pangi.domain.runs import RunMode


class PlanValidationErrorCode(StrEnum):
    INVALID_MODE_PAYLOAD = "invalid_mode_payload"
    INVALID_COMPOSITION = "invalid_composition"
    TASK_LIMIT_EXCEEDED = "task_limit_exceeded"
    DUPLICATE_TASK_ID = "duplicate_task_id"
    UNKNOWN_SUBAGENT = "unknown_subagent"
    UNKNOWN_SKILL = "unknown_skill"
    DUPLICATE_DEPENDENCY = "duplicate_dependency"
    SELF_DEPENDENCY = "self_dependency"
    UNKNOWN_DEPENDENCY = "unknown_dependency"
    DEPENDENCY_CYCLE = "dependency_cycle"
    DUPLICATE_HINT = "duplicate_hint"
    TASK_TIMEOUT_EXCEEDED = "task_timeout_exceeded"
    RUN_TIMEOUT_EXCEEDED = "run_timeout_exceeded"
    INVALID_SYNTHESIS = "invalid_synthesis"


class PlanValidationError(ValueError):
    """A deterministic rejection that never includes model or user content."""

    def __init__(self, code: PlanValidationErrorCode) -> None:
        super().__init__(f"Orchestrator plan rejected: {code.value}")
        self.code = code


class OrchestratorPlanValidator:
    def __init__(
        self,
        *,
        catalog: OrchestratorCatalog,
        limits: OrchestratorLimits | None = None,
    ) -> None:
        self._catalog = catalog
        self._limits = limits or OrchestratorLimits()

    def validate(self, decision: OrchestratorDecision) -> ValidatedOrchestratorPlan:
        if decision.mode is RunMode.DIRECT:
            return self._validate_direct(decision)
        if decision.mode is RunMode.SKILL:
            return self._validate_skill(decision)
        return self._validate_delegate(decision)

    def _validate_direct(
        self,
        decision: OrchestratorDecision,
    ) -> ValidatedOrchestratorPlan:
        if decision.direct_answer is None or decision.skill_name is not None or decision.tasks:
            _reject(PlanValidationErrorCode.INVALID_MODE_PAYLOAD)
        if decision.composition is not CompositionMode.DETERMINISTIC:
            _reject(PlanValidationErrorCode.INVALID_COMPOSITION)
        return ValidatedOrchestratorPlan(decision, (), 0)

    def _validate_skill(
        self,
        decision: OrchestratorDecision,
    ) -> ValidatedOrchestratorPlan:
        if decision.direct_answer is not None or decision.skill_name is None or decision.tasks:
            _reject(PlanValidationErrorCode.INVALID_MODE_PAYLOAD)
        if decision.composition is not CompositionMode.DETERMINISTIC:
            _reject(PlanValidationErrorCode.INVALID_COMPOSITION)
        if decision.skill_name not in self._catalog.active_skills:
            _reject(PlanValidationErrorCode.UNKNOWN_SKILL)
        return ValidatedOrchestratorPlan(decision, (), 0)

    def _validate_delegate(
        self,
        decision: OrchestratorDecision,
    ) -> ValidatedOrchestratorPlan:
        if (
            decision.direct_answer is not None
            or decision.skill_name is not None
            or not decision.tasks
        ):
            _reject(PlanValidationErrorCode.INVALID_MODE_PAYLOAD)
        if len(decision.tasks) > self._limits.max_tasks:
            _reject(PlanValidationErrorCode.TASK_LIMIT_EXCEEDED)

        task_ids = tuple(task.id for task in decision.tasks)
        if len(set(task_ids)) != len(task_ids):
            _reject(PlanValidationErrorCode.DUPLICATE_TASK_ID)
        known_task_ids = frozenset(task_ids)

        for task in decision.tasks:
            self._validate_task(task, known_task_ids=known_task_ids)

        ordered_tasks = _topological_order(decision.tasks)
        self._validate_synthesis(decision)
        critical_path = _critical_path_timeout(ordered_tasks)
        if critical_path > self._limits.run_timeout_seconds:
            _reject(PlanValidationErrorCode.RUN_TIMEOUT_EXCEEDED)
        return ValidatedOrchestratorPlan(decision, ordered_tasks, critical_path)

    def _validate_task(
        self,
        task: DelegatedTask,
        *,
        known_task_ids: frozenset[str],
    ) -> None:
        if task.subagent not in self._catalog.available_subagents:
            _reject(PlanValidationErrorCode.UNKNOWN_SUBAGENT)
        if task.timeout_seconds > self._limits.max_task_timeout_seconds:
            _reject(PlanValidationErrorCode.TASK_TIMEOUT_EXCEEDED)
        if len(set(task.depends_on)) != len(task.depends_on):
            _reject(PlanValidationErrorCode.DUPLICATE_DEPENDENCY)
        if task.id in task.depends_on:
            _reject(PlanValidationErrorCode.SELF_DEPENDENCY)
        if any(dependency not in known_task_ids for dependency in task.depends_on):
            _reject(PlanValidationErrorCode.UNKNOWN_DEPENDENCY)
        for hints in (task.connection_hints, task.allowed_tool_hints):
            if len(set(hints)) != len(hints):
                _reject(PlanValidationErrorCode.DUPLICATE_HINT)

    def _validate_synthesis(self, decision: OrchestratorDecision) -> None:
        synthesis_tasks = tuple(task for task in decision.tasks if task.subagent == "synthesis")
        if decision.composition is CompositionMode.DETERMINISTIC:
            if synthesis_tasks:
                _reject(PlanValidationErrorCode.INVALID_SYNTHESIS)
            return
        if len(synthesis_tasks) != 1 or "synthesis" not in self._catalog.available_subagents:
            _reject(PlanValidationErrorCode.INVALID_SYNTHESIS)
        synthesis_task = synthesis_tasks[0]
        if len(synthesis_task.depends_on) < 2:
            _reject(PlanValidationErrorCode.INVALID_SYNTHESIS)
        if any(
            synthesis_task.id in task.depends_on
            for task in decision.tasks
            if task.id != synthesis_task.id
        ):
            _reject(PlanValidationErrorCode.INVALID_SYNTHESIS)


def _topological_order(tasks: tuple[DelegatedTask, ...]) -> tuple[DelegatedTask, ...]:
    completed: set[str] = set()
    remaining = {task.id for task in tasks}
    ordered: list[DelegatedTask] = []
    while remaining:
        progressed = False
        for task in tasks:
            if task.id not in remaining or not set(task.depends_on).issubset(completed):
                continue
            ordered.append(task)
            completed.add(task.id)
            remaining.remove(task.id)
            progressed = True
        if not progressed:
            _reject(PlanValidationErrorCode.DEPENDENCY_CYCLE)
    return tuple(ordered)


def _critical_path_timeout(tasks: tuple[DelegatedTask, ...]) -> int:
    durations: dict[str, int] = {}
    for task in tasks:
        dependency_duration = max(
            (durations[dependency] for dependency in task.depends_on),
            default=0,
        )
        durations[task.id] = dependency_duration + task.timeout_seconds
    return max(durations.values(), default=0)


def _reject(code: PlanValidationErrorCode) -> None:
    raise PlanValidationError(code)
