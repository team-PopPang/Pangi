"""Deterministic orchestration result reduction and final Output sanitization."""

from __future__ import annotations

import unicodedata
from collections import Counter
from enum import StrEnum
from typing import NoReturn, Protocol

from pangi.application.contracts.orchestration import (
    AgentResult,
    AgentResultStatus,
    CompositionMode,
    Evidence,
)
from pangi.application.contracts.orchestration_execution import (
    ExecutionOutcome,
    PreparedExecutionPlan,
    PreparedExecutionStep,
)
from pangi.application.contracts.output_guardrails import (
    OutputCandidate,
    OutputGuardrailBlockedError,
    SafeOutput,
)
from pangi.domain.runs import RunErrorCode, RunMode, RunState, StepRequirement


class OrchestrationCompositionErrorCode(StrEnum):
    OUTCOME_NOT_COMPOSABLE = "outcome_not_composable"
    MODE_MISMATCH = "composition_mode_mismatch"
    RESULT_SET_INVALID = "composition_result_set_invalid"
    OUTCOME_METADATA_INVALID = "composition_outcome_metadata_invalid"
    SYNTHESIS_RESULT_INVALID = "synthesis_result_invalid"
    OUTPUT_GUARDRAIL_FAILED = "output_guardrail_failed"


class OrchestrationCompositionError(ValueError):
    """A stable composition failure that never includes proposed Output content."""

    def __init__(self, code: OrchestrationCompositionErrorCode) -> None:
        super().__init__(f"Orchestration composition failed: {code.value}")
        self.code = code


class OutputGuardrail(Protocol):
    def sanitize(self, candidate: OutputCandidate) -> SafeOutput:
        """Return a sanitized final Output or raise a safe rejection."""

        ...


class DeterministicResultReducer:
    """Reduce one validated execution outcome without making new decisions."""

    def reduce(
        self,
        plan: PreparedExecutionPlan,
        outcome: ExecutionOutcome,
    ) -> OutputCandidate:
        if not isinstance(plan, PreparedExecutionPlan):
            raise TypeError("plan must be PreparedExecutionPlan")
        if not isinstance(outcome, ExecutionOutcome):
            raise TypeError("outcome must be ExecutionOutcome")
        if outcome.state is not RunState.COMPOSING:
            _reject(OrchestrationCompositionErrorCode.OUTCOME_NOT_COMPOSABLE)
        if plan.mode is RunMode.DIRECT:
            return self._reduce_direct(plan, outcome)
        if plan.mode is not RunMode.DELEGATE:
            _reject(OrchestrationCompositionErrorCode.MODE_MISMATCH)
        return self._reduce_delegate(plan, outcome)

    @staticmethod
    def _reduce_direct(
        plan: PreparedExecutionPlan,
        outcome: ExecutionOutcome,
    ) -> OutputCandidate:
        if (
            plan.direct_answer is None
            or outcome.direct_answer != plan.direct_answer
            or outcome.results
            or outcome.warnings
            or outcome.error_code is not None
        ):
            _reject(OrchestrationCompositionErrorCode.MODE_MISMATCH)
        return OutputCandidate(markdown=plan.direct_answer)

    def _reduce_delegate(
        self,
        plan: PreparedExecutionPlan,
        outcome: ExecutionOutcome,
    ) -> OutputCandidate:
        if outcome.direct_answer is not None or not outcome.results:
            _reject(OrchestrationCompositionErrorCode.MODE_MISMATCH)
        results = _ordered_results(plan, outcome)
        optional_failures = _optional_failures(plan.steps, results)
        expected_error = RunErrorCode.OPTIONAL_STEP_FAILED if optional_failures else None
        if outcome.error_code is not expected_error:
            _reject(OrchestrationCompositionErrorCode.OUTCOME_METADATA_INVALID)

        evidence, source_indices = _collect_evidence(results)
        body_results = self._body_results(plan, results)
        warnings = _collect_warnings(
            plan.steps,
            results,
            outcome,
            optional_failures=optional_failures,
        )
        markdown = _render_markdown(
            body_results,
            evidence,
            source_indices,
            warnings,
            synthesis=plan.composition is CompositionMode.SYNTHESIS_SUBAGENT,
        )
        evidence_links = tuple(uri for _, uri in evidence if uri is not None)
        return OutputCandidate(markdown=markdown, evidence_links=evidence_links)

    @staticmethod
    def _body_results(
        plan: PreparedExecutionPlan,
        results: tuple[AgentResult | None, ...],
    ) -> tuple[AgentResult, ...]:
        synthesis_steps = tuple(step for step in plan.steps if step.task.subagent == "synthesis")
        if plan.composition is CompositionMode.DETERMINISTIC:
            if synthesis_steps:
                _reject(OrchestrationCompositionErrorCode.SYNTHESIS_RESULT_INVALID)
            return tuple(
                result
                for result in results
                if result is not None and result.status is not AgentResultStatus.FAILED
            )

        if len(synthesis_steps) != 1:
            _reject(OrchestrationCompositionErrorCode.SYNTHESIS_RESULT_INVALID)
        synthesis_step = synthesis_steps[0]
        if len(synthesis_step.task.depends_on) < 2 or any(
            synthesis_step.task.id in step.task.depends_on
            for step in plan.steps
            if step.task.id != synthesis_step.task.id
        ):
            _reject(OrchestrationCompositionErrorCode.SYNTHESIS_RESULT_INVALID)
        result_by_id = {result.task_id: result for result in results if result is not None}
        synthesis_result = result_by_id.get(synthesis_step.task.id)
        if synthesis_result is None or synthesis_result.status is AgentResultStatus.FAILED:
            _reject(OrchestrationCompositionErrorCode.SYNTHESIS_RESULT_INVALID)
        return (synthesis_result,)


class OrchestrationOutputComposer:
    """Expose only Output Guardrail-approved orchestration content."""

    def __init__(
        self,
        output_guardrail: OutputGuardrail,
        *,
        reducer: DeterministicResultReducer | None = None,
    ) -> None:
        self._output_guardrail = output_guardrail
        self._reducer = reducer or DeterministicResultReducer()

    def compose(
        self,
        plan: PreparedExecutionPlan,
        outcome: ExecutionOutcome,
    ) -> SafeOutput:
        candidate = self._reducer.reduce(plan, outcome)
        try:
            safe: object = self._output_guardrail.sanitize(candidate)
        except OutputGuardrailBlockedError:
            raise
        except Exception:
            raise OrchestrationCompositionError(
                OrchestrationCompositionErrorCode.OUTPUT_GUARDRAIL_FAILED
            ) from None
        if not isinstance(safe, SafeOutput):
            _reject(OrchestrationCompositionErrorCode.OUTPUT_GUARDRAIL_FAILED)
        return safe


def _ordered_results(
    plan: PreparedExecutionPlan,
    outcome: ExecutionOutcome,
) -> tuple[AgentResult | None, ...]:
    result_ids = tuple(result.task_id for result in outcome.results)
    plan_ids = tuple(step.task.id for step in plan.steps)
    if len(set(result_ids)) != len(result_ids) or not set(result_ids).issubset(plan_ids):
        _reject(OrchestrationCompositionErrorCode.RESULT_SET_INVALID)
    result_by_id = {result.task_id: result for result in outcome.results}
    ordered = tuple(result_by_id.get(task_id) for task_id in plan_ids)
    for step, result in zip(plan.steps, ordered, strict=True):
        if result is None or result.status is AgentResultStatus.FAILED:
            if step.requirement is StepRequirement.REQUIRED:
                _reject(OrchestrationCompositionErrorCode.RESULT_SET_INVALID)
    return ordered


def _optional_failures(
    steps: tuple[PreparedExecutionStep, ...],
    results: tuple[AgentResult | None, ...],
) -> tuple[PreparedExecutionStep, ...]:
    return tuple(
        step
        for step, result in zip(steps, results, strict=True)
        if result is None or result.status is AgentResultStatus.FAILED
    )


def _collect_evidence(
    results: tuple[AgentResult | None, ...],
) -> tuple[
    tuple[tuple[Evidence, str | None], ...],
    dict[str, tuple[int, ...]],
]:
    collected: list[tuple[Evidence, str | None]] = []
    uri_indices: dict[str, int] = {}
    indices_by_task: dict[str, tuple[int, ...]] = {}
    for result in results:
        if result is None:
            continue
        task_indices: list[int] = []
        for evidence in result.evidence:
            normalized_uri = _normalized_uri(evidence.uri)
            if normalized_uri is not None and normalized_uri in uri_indices:
                index = uri_indices[normalized_uri]
            else:
                collected.append((evidence, normalized_uri))
                index = len(collected)
                if normalized_uri is not None:
                    uri_indices[normalized_uri] = index
            if index not in task_indices:
                task_indices.append(index)
        indices_by_task[result.task_id] = tuple(task_indices)
    return tuple(collected), indices_by_task


def _collect_warnings(
    steps: tuple[PreparedExecutionStep, ...],
    results: tuple[AgentResult | None, ...],
    outcome: ExecutionOutcome,
    *,
    optional_failures: tuple[PreparedExecutionStep, ...],
) -> tuple[str, ...]:
    known_raw: Counter[str] = Counter()
    rendered: list[str] = []
    optional_ids = {step.task.id for step in optional_failures}
    for step, result in zip(steps, results, strict=True):
        task_id = step.task.id
        if task_id in optional_ids:
            known_raw[f"optional step failed: {task_id}"] += 1
            error_code = "dependency_failed"
            if result is not None:
                if result.error_code is None:
                    _reject(OrchestrationCompositionErrorCode.OUTCOME_METADATA_INVALID)
                error_code = result.error_code
            rendered.append(f"[{task_id}] optional step failed: {error_code}")
        if result is None:
            continue
        if result.status is AgentResultStatus.PARTIAL:
            known_raw[f"partial result: {task_id}"] += 1
            rendered.append(f"[{task_id}] partial result")
        if result.error_code is not None and result.status is not AgentResultStatus.FAILED:
            rendered.append(f"[{task_id}] {result.error_code}")
        for warning in result.warnings:
            known_raw[warning] += 1
            rendered.append(f"[{task_id}] {_inline_text(warning)}")

    remaining_known = known_raw.copy()
    for warning in outcome.warnings:
        if remaining_known[warning]:
            remaining_known[warning] -= 1
        else:
            rendered.append(f"[run] {_inline_text(warning)}")
    return tuple(rendered)


def _render_markdown(
    body_results: tuple[AgentResult, ...],
    evidence: tuple[tuple[Evidence, str | None], ...],
    source_indices: dict[str, tuple[int, ...]],
    warnings: tuple[str, ...],
    *,
    synthesis: bool,
) -> str:
    sections: list[str] = ["## Results"]
    if body_results:
        all_sources = tuple(range(1, len(evidence) + 1))
        for result in body_results:
            sections.append(f"### {result.task_id}\n\n{result.summary_markdown.strip()}")
            indices = all_sources if synthesis else source_indices.get(result.task_id, ())
            if indices:
                references = ", ".join(f"[{index}]" for index in indices)
                sections.append(f"Sources: {references}")
    else:
        sections.append("No completed task result is available.")

    if warnings:
        warning_lines = "\n".join(f"- {warning}" for warning in warnings)
        sections.append(f"## Warnings\n\n{warning_lines}")
    if evidence:
        source_lines = "\n".join(
            f"{index}. {_inline_text(item.title)} — {item.source_type.value}:{item.source_name}"
            for index, (item, _) in enumerate(evidence, start=1)
        )
        sections.append(f"## Sources\n\n{source_lines}")
    return "\n\n".join(sections)


def _normalized_uri(value: str | None) -> str | None:
    if value is None:
        return None
    return unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n").strip(),
    )


def _inline_text(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n"),
    )
    single_line = " ".join(normalized.split())
    for character in ("\\", "`", "*", "_", "[", "]", "<", ">"):
        single_line = single_line.replace(character, f"\\{character}")
    return single_line


def _reject(code: OrchestrationCompositionErrorCode) -> NoReturn:
    raise OrchestrationCompositionError(code)
