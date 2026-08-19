"""Durable orchestration execution contract tests."""

from datetime import UTC, datetime

import pytest

from pangi.application.contracts.orchestration import (
    AgentResult,
    AgentResultStatus,
    CompositionMode,
    DelegatedTask,
    Evidence,
    EvidenceSourceType,
    OrchestratorDecision,
    ValidatedOrchestratorPlan,
)
from pangi.application.contracts.orchestration_execution import (
    ExecutionOutcome,
    ExecutionPolicy,
    ExecutionStepSnapshot,
    PreparedExecutionPlan,
    PreparedExecutionStep,
    StepExecutionRequest,
    agent_result_data,
    agent_result_from_data,
    execution_plan_data,
    prepared_execution_plan_from_data,
)
from pangi.domain.runs import (
    RunErrorCode,
    RunMode,
    RunState,
    RunStep,
    StepRequirement,
    StepState,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _tasks() -> tuple[DelegatedTask, DelegatedTask]:
    first = DelegatedTask(
        id="collect",
        subagent="github-research",
        objective="Collect current issues.",
    )
    second = DelegatedTask(
        id="summarize",
        subagent="synthesis",
        objective="Summarize collected issues.",
        depends_on=("collect",),
    )
    return first, second


def _result(task_id: str) -> AgentResult:
    return AgentResult(
        task_id=task_id,
        status=AgentResultStatus.SUCCEEDED,
        summary_markdown="Completed summary.",
        evidence=(
            Evidence(
                source_type=EvidenceSourceType.MCP,
                source_name="github",
                title="Issue list",
                uri="https://example.com/issues",
            ),
        ),
        facts=({"open": 3},),
    )


def test_validated_root_plan_becomes_required_non_idempotent_execution() -> None:
    tasks = _tasks()
    decision = OrchestratorDecision(mode=RunMode.DELEGATE, tasks=tasks)
    validated = ValidatedOrchestratorPlan(decision, tasks, 120)

    prepared = PreparedExecutionPlan.from_validated(validated)

    assert tuple(step.task.id for step in prepared.steps) == ("collect", "summarize")
    assert all(step.requirement is StepRequirement.REQUIRED for step in prepared.steps)
    assert all(not step.idempotent for step in prepared.steps)
    assert len(prepared.fingerprint) == 64


def test_execution_plan_round_trip_is_canonical_and_secret_safe() -> None:
    secret = "private-objective-token"
    first, second = _tasks()
    plan = PreparedExecutionPlan(
        mode=RunMode.DELEGATE,
        steps=(
            PreparedExecutionStep(
                DelegatedTask(
                    id=first.id,
                    subagent=first.subagent,
                    objective=f"Collect {secret}",
                ),
                idempotent=True,
            ),
            PreparedExecutionStep(second, requirement=StepRequirement.OPTIONAL),
        ),
        composition=CompositionMode.DETERMINISTIC,
    )

    restored = prepared_execution_plan_from_data(execution_plan_data(plan))

    assert restored == plan
    assert restored.fingerprint == plan.fingerprint
    assert secret not in repr(plan)
    assert secret not in repr(restored)
    with pytest.raises(ValueError, match="dependency order"):
        PreparedExecutionPlan(
            mode=RunMode.DELEGATE,
            steps=tuple(reversed(plan.steps)),
        )


def test_agent_result_round_trip_and_dependency_request_preserve_order() -> None:
    collect = _result("collect")
    restored = agent_result_from_data(agent_result_data(collect))
    request = StepExecutionRequest(
        run_id="run-execution-0001",
        step_id="step-execution-0001",
        task=_tasks()[1],
        dependency_results=(restored,),
    )

    assert restored == collect
    assert request.dependency_results == (collect,)
    with pytest.raises(ValueError, match="dependency order"):
        StepExecutionRequest(
            run_id=request.run_id,
            step_id=request.step_id,
            task=request.task,
            dependency_results=(),
        )


def test_execution_snapshot_and_outcome_reject_cross_step_or_invalid_state() -> None:
    definition = PreparedExecutionStep(_tasks()[0])
    step = RunStep(
        id="step-execution-0001",
        run_id="run-execution-0001",
        node_id="collect",
        type="subagent",
        state=StepState.COMPLETED,
        requirement=StepRequirement.REQUIRED,
        idempotent=False,
        attempt=1,
        created_at=NOW,
        updated_at=NOW,
        finished_at=NOW,
    )
    snapshot = ExecutionStepSnapshot(step, definition, _result("collect"))
    outcome = ExecutionOutcome(
        run_id=step.run_id,
        state=RunState.COMPOSING,
        results=(snapshot.result,),  # type: ignore[arg-type]
    )

    assert outcome.error_code is None
    with pytest.raises(ValueError, match="another Step"):
        ExecutionStepSnapshot(step, definition, _result("other"))
    with pytest.raises(ValueError, match="requires an AgentResult"):
        ExecutionStepSnapshot(step, definition)
    with pytest.raises(ValueError, match="do not match"):
        ExecutionStepSnapshot(
            step,
            definition,
            AgentResult(
                task_id="collect",
                status=AgentResultStatus.FAILED,
                summary_markdown="Failed summary.",
                error_code="step_execution_failed",
            ),
        )
    with pytest.raises(ValueError, match="required_step_failed"):
        ExecutionOutcome(
            run_id=step.run_id,
            state=RunState.FAILED,
            error_code=RunErrorCode.OPTIONAL_STEP_FAILED,
        )


def test_execution_policy_and_direct_contract_are_bounded() -> None:
    assert ExecutionPolicy(3).max_parallel_steps == 3
    direct = PreparedExecutionPlan(
        mode=RunMode.DIRECT,
        direct_answer="Hello.",
    )
    assert prepared_execution_plan_from_data(execution_plan_data(direct)) == direct

    for invalid in (0, 6, True):
        with pytest.raises(ValueError, match="between 1 and 5"):
            ExecutionPolicy(invalid)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Skill runtime"):
        PreparedExecutionPlan(mode=RunMode.SKILL)
