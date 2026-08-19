"""Fail-closed Root plan validation unit tests."""

from collections.abc import Callable

import pytest

from pangi.application.contracts.orchestration import (
    CompositionMode,
    DelegatedTask,
    OrchestratorCatalog,
    OrchestratorDecision,
    OrchestratorLimits,
)
from pangi.application.services.plan_validator import (
    OrchestratorPlanValidator,
    PlanValidationError,
    PlanValidationErrorCode,
)
from pangi.domain.runs import RunMode

CATALOG = OrchestratorCatalog(
    available_subagents=frozenset({"github-research", "notion-research", "synthesis"}),
    active_skills=frozenset({"weekly-summary"}),
)


def _validator(*, limits: OrchestratorLimits | None = None) -> OrchestratorPlanValidator:
    return OrchestratorPlanValidator(catalog=CATALOG, limits=limits)


def _task(
    task_id: str,
    *,
    subagent: str = "github-research",
    depends_on: tuple[str, ...] = (),
    connection_hints: tuple[str, ...] = (),
    allowed_tool_hints: tuple[str, ...] = (),
    timeout_seconds: int = 60,
    objective: str = "Collect the requested source.",
) -> DelegatedTask:
    return DelegatedTask(
        id=task_id,
        subagent=subagent,
        objective=objective,
        depends_on=depends_on,
        connection_hints=connection_hints,
        allowed_tool_hints=allowed_tool_hints,
        timeout_seconds=timeout_seconds,
    )


def _delegate(
    *tasks: DelegatedTask,
    composition: CompositionMode = CompositionMode.DETERMINISTIC,
) -> OrchestratorDecision:
    return OrchestratorDecision(
        mode=RunMode.DELEGATE,
        tasks=tasks,
        composition=composition,
    )


def _assert_rejected(
    decision: OrchestratorDecision,
    code: PlanValidationErrorCode,
    *,
    validator: OrchestratorPlanValidator | None = None,
) -> None:
    with pytest.raises(PlanValidationError) as captured:
        (validator or _validator()).validate(decision)
    assert captured.value.code is code


def test_valid_direct_skill_and_delegate_modes_return_bounded_plans() -> None:
    direct = _validator().validate(
        OrchestratorDecision(mode=RunMode.DIRECT, direct_answer="Hello.")
    )
    skill = _validator().validate(
        OrchestratorDecision(mode=RunMode.SKILL, skill_name="weekly-summary")
    )
    delegate = _validator().validate(_delegate(_task("collect-issues")))

    assert direct.ordered_tasks == ()
    assert skill.ordered_tasks == ()
    assert tuple(task.id for task in delegate.ordered_tasks) == ("collect-issues",)
    assert delegate.critical_path_timeout_seconds == 60


@pytest.mark.parametrize(
    ("decision", "code"),
    (
        (
            OrchestratorDecision(mode=RunMode.DIRECT),
            PlanValidationErrorCode.INVALID_MODE_PAYLOAD,
        ),
        (
            OrchestratorDecision(
                mode=RunMode.DIRECT,
                direct_answer="Answer.",
                skill_name="weekly-summary",
            ),
            PlanValidationErrorCode.INVALID_MODE_PAYLOAD,
        ),
        (
            OrchestratorDecision(mode=RunMode.SKILL),
            PlanValidationErrorCode.INVALID_MODE_PAYLOAD,
        ),
        (
            OrchestratorDecision(mode=RunMode.SKILL, skill_name="unknown-skill"),
            PlanValidationErrorCode.UNKNOWN_SKILL,
        ),
        (
            OrchestratorDecision(mode=RunMode.DELEGATE),
            PlanValidationErrorCode.INVALID_MODE_PAYLOAD,
        ),
        (
            OrchestratorDecision(
                mode=RunMode.DIRECT,
                direct_answer="Answer.",
                composition=CompositionMode.SYNTHESIS_SUBAGENT,
            ),
            PlanValidationErrorCode.INVALID_COMPOSITION,
        ),
    ),
)
def test_mode_payloads_are_mutually_exclusive(
    decision: OrchestratorDecision,
    code: PlanValidationErrorCode,
) -> None:
    _assert_rejected(decision, code)


@pytest.mark.parametrize(
    ("decision_factory", "code"),
    (
        (
            lambda: _delegate(_task("same"), _task("same")),
            PlanValidationErrorCode.DUPLICATE_TASK_ID,
        ),
        (
            lambda: _delegate(_task("unknown", subagent="missing-agent")),
            PlanValidationErrorCode.UNKNOWN_SUBAGENT,
        ),
        (
            lambda: _delegate(_task("self", depends_on=("self",))),
            PlanValidationErrorCode.SELF_DEPENDENCY,
        ),
        (
            lambda: _delegate(_task("child", depends_on=("missing",))),
            PlanValidationErrorCode.UNKNOWN_DEPENDENCY,
        ),
        (
            lambda: _delegate(
                _task("parent"),
                _task("child", depends_on=("parent", "parent")),
            ),
            PlanValidationErrorCode.DUPLICATE_DEPENDENCY,
        ),
        (
            lambda: _delegate(
                _task("hinted", connection_hints=("github", "github")),
            ),
            PlanValidationErrorCode.DUPLICATE_HINT,
        ),
        (
            lambda: _delegate(
                _task("first", depends_on=("second",)),
                _task("second", depends_on=("first",)),
            ),
            PlanValidationErrorCode.DEPENDENCY_CYCLE,
        ),
    ),
)
def test_invalid_task_graphs_fail_closed(
    decision_factory: Callable[[], OrchestratorDecision],
    code: PlanValidationErrorCode,
) -> None:
    _assert_rejected(decision_factory(), code)


def test_topological_order_uses_decision_order_as_the_stable_tie_breaker() -> None:
    decision = _delegate(
        _task("final", depends_on=("github", "notion")),
        _task("notion", subagent="notion-research"),
        _task("github"),
    )

    first = _validator().validate(decision)
    second = _validator().validate(decision)

    assert tuple(task.id for task in first.ordered_tasks) == ("notion", "github", "final")
    assert first == second
    assert first.critical_path_timeout_seconds == 120


def test_task_count_timeout_and_critical_path_limits_are_independent() -> None:
    tasks = tuple(_task(f"task-{index}") for index in range(4))
    _assert_rejected(_delegate(*tasks), PlanValidationErrorCode.TASK_LIMIT_EXCEEDED)

    timeout_validator = _validator(limits=OrchestratorLimits(max_task_timeout_seconds=30))
    _assert_rejected(
        _delegate(_task("slow", timeout_seconds=31)),
        PlanValidationErrorCode.TASK_TIMEOUT_EXCEEDED,
        validator=timeout_validator,
    )

    run_validator = _validator(limits=OrchestratorLimits(run_timeout_seconds=100))
    _assert_rejected(
        _delegate(
            _task("first", timeout_seconds=60),
            _task("second", depends_on=("first",), timeout_seconds=60),
        ),
        PlanValidationErrorCode.RUN_TIMEOUT_EXCEEDED,
        validator=run_validator,
    )

    parallel = run_validator.validate(
        _delegate(
            _task("first", timeout_seconds=60),
            _task("second", timeout_seconds=60),
        )
    )
    assert parallel.critical_path_timeout_seconds == 60


def test_operational_limits_cannot_exceed_orchestrator_hard_caps() -> None:
    with pytest.raises(ValueError, match="max_tasks"):
        OrchestratorLimits(max_tasks=6)
    with pytest.raises(ValueError, match="max_task_timeout_seconds"):
        OrchestratorLimits(max_task_timeout_seconds=181)
    with pytest.raises(ValueError, match="run_timeout_seconds"):
        OrchestratorLimits(run_timeout_seconds=601)
    with pytest.raises(ValueError, match="task timeout_seconds"):
        _task("too-slow", timeout_seconds=181)


def test_synthesis_must_be_a_registered_terminal_task_with_two_inputs() -> None:
    valid = _delegate(
        _task("github"),
        _task("notion", subagent="notion-research"),
        _task(
            "compose",
            subagent="synthesis",
            depends_on=("github", "notion"),
        ),
        composition=CompositionMode.SYNTHESIS_SUBAGENT,
    )
    plan = _validator().validate(valid)
    assert tuple(task.id for task in plan.ordered_tasks) == ("github", "notion", "compose")

    _assert_rejected(
        _delegate(
            _task("github"),
            _task("compose", subagent="synthesis", depends_on=("github",)),
            composition=CompositionMode.SYNTHESIS_SUBAGENT,
        ),
        PlanValidationErrorCode.INVALID_SYNTHESIS,
    )
    _assert_rejected(
        _delegate(_task("compose", subagent="synthesis")),
        PlanValidationErrorCode.INVALID_SYNTHESIS,
    )


def test_hints_are_not_treated_as_registry_authority() -> None:
    plan = _validator().validate(
        _delegate(
            _task(
                "collect",
                connection_hints=("not-yet-connected",),
                allowed_tool_hints=("unregistered.tool",),
            )
        )
    )

    assert plan.ordered_tasks[0].connection_hints == ("not-yet-connected",)


def test_validation_errors_and_representations_do_not_expose_objectives() -> None:
    objective_secret = "private delegated objective"
    decision = _delegate(_task("unknown", subagent="missing-agent", objective=objective_secret))

    with pytest.raises(PlanValidationError) as captured:
        _validator().validate(decision)

    assert captured.value.code is PlanValidationErrorCode.UNKNOWN_SUBAGENT
    assert objective_secret not in str(captured.value)
    assert objective_secret not in repr(captured.value)
    assert objective_secret not in repr(decision)
