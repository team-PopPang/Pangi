"""Deterministic orchestration reduction and safe final composition tests."""

from itertools import permutations

import pytest

from pangi.application.contracts.orchestration import (
    AgentResult,
    AgentResultStatus,
    CompositionMode,
    DelegatedTask,
    Evidence,
    EvidenceSourceType,
)
from pangi.application.contracts.orchestration_execution import (
    ExecutionOutcome,
    PreparedExecutionPlan,
    PreparedExecutionStep,
)
from pangi.application.contracts.output_guardrails import (
    OutputCandidate,
    OutputGuardrailBlockedError,
    OutputGuardrailPolicy,
)
from pangi.application.services.output_guardrails import (
    OutputGuardrailService,
    core_output_internal_detail_rules,
)
from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)
from pangi.application.services.result_reducer import (
    DeterministicResultReducer,
    OrchestrationCompositionError,
    OrchestrationCompositionErrorCode,
    OrchestrationOutputComposer,
)
from pangi.domain.runs import (
    RunErrorCode,
    RunMode,
    RunState,
    StepRequirement,
)

RUN_ID = "run-composition-0001"


def _task(
    task_id: str,
    *,
    subagent: str = "research",
    depends_on: tuple[str, ...] = (),
) -> DelegatedTask:
    return DelegatedTask(
        id=task_id,
        subagent=subagent,
        objective=f"Complete {task_id}.",
        depends_on=depends_on,
    )


def _result(
    task_id: str,
    summary: str,
    *,
    status: AgentResultStatus = AgentResultStatus.SUCCEEDED,
    evidence: tuple[Evidence, ...] = (),
    warnings: tuple[str, ...] = (),
    error_code: str | None = None,
) -> AgentResult:
    return AgentResult(
        task_id=task_id,
        status=status,
        summary_markdown=summary,
        evidence=evidence,
        warnings=warnings,
        error_code=error_code,
    )


def _evidence(title: str, uri: str | None, *, source: str = "github") -> Evidence:
    return Evidence(
        source_type=EvidenceSourceType.MCP,
        source_name=source,
        title=title,
        uri=uri,
    )


def _output_guardrail() -> OutputGuardrailService:
    return OutputGuardrailService(
        OutputGuardrailPolicy(
            policy_version="orchestration-output-v1",
            max_input_bytes=100_000,
            max_output_bytes=50_000,
            max_mentions=2,
            max_evidence_links=20,
            max_evidence_link_bytes=2_048,
            allowed_link_schemes=frozenset({"https"}),
            allow_relative_links=False,
            broadcast_mentions=frozenset({"@channel", "@everyone", "@here"}),
            internal_detail_rules=core_output_internal_detail_rules(),
            truncation_marker="\n\n[OUTPUT TRUNCATED]",
        ),
        redactor=RedactionService(core_secret_redaction_policy()),
    )


def test_result_permutations_keep_plan_order_evidence_and_fingerprint() -> None:
    plan = PreparedExecutionPlan(
        mode=RunMode.DELEGATE,
        steps=(
            PreparedExecutionStep(_task("collect")),
            PreparedExecutionStep(_task("analyze", depends_on=("collect",))),
            PreparedExecutionStep(
                _task("archive", depends_on=("analyze",)),
                requirement=StepRequirement.OPTIONAL,
            ),
        ),
    )
    duplicated_uri = "https://example.com/cafe\u0301"
    results = (
        _result(
            "collect",
            "Collected facts.",
            evidence=(_evidence("Primary source", duplicated_uri),),
        ),
        _result(
            "analyze",
            "Analyzed facts.",
            status=AgentResultStatus.PARTIAL,
            evidence=(
                _evidence("Duplicate source", "https://example.com/café"),
                _evidence("Analysis source", "https://example.com/analysis"),
            ),
            warnings=("Some records were unavailable.",),
            error_code="records_incomplete",
        ),
        _result(
            "archive",
            "Archiving failed.",
            status=AgentResultStatus.FAILED,
            evidence=(_evidence("Archive status", "https://example.com/archive"),),
            error_code="archive_failed",
        ),
    )
    reducer = DeterministicResultReducer()
    composer = OrchestrationOutputComposer(_output_guardrail(), reducer=reducer)
    candidates: set[tuple[str, tuple[str, ...]]] = set()
    fingerprints: set[str] = set()

    for ordered_results in permutations(results):
        outcome = ExecutionOutcome(
            run_id=RUN_ID,
            state=RunState.COMPOSING,
            results=ordered_results,
            warnings=(
                "optional step failed: archive",
                "partial result: analyze",
                "Some records were unavailable.",
            ),
            error_code=RunErrorCode.OPTIONAL_STEP_FAILED,
        )
        candidate = reducer.reduce(plan, outcome)
        safe = composer.compose(plan, outcome)
        candidates.add((candidate.markdown, candidate.evidence_links))
        fingerprints.add(safe.content_fingerprint)

    assert len(candidates) == 1
    assert len(fingerprints) == 1
    markdown, links = candidates.pop()
    assert markdown.index("### collect") < markdown.index("### analyze")
    assert "Archiving failed." not in markdown
    assert "[analyze] partial result" in markdown
    assert "[archive] optional step failed: archive_failed" in markdown
    assert "Duplicate source" not in markdown
    assert links == (
        "https://example.com/café",
        "https://example.com/analysis",
        "https://example.com/archive",
    )


def test_synthesis_uses_only_terminal_summary_and_all_ordered_sources() -> None:
    plan = PreparedExecutionPlan(
        mode=RunMode.DELEGATE,
        steps=(
            PreparedExecutionStep(_task("github")),
            PreparedExecutionStep(_task("notion", subagent="notion-research")),
            PreparedExecutionStep(
                _task(
                    "compose",
                    subagent="synthesis",
                    depends_on=("github", "notion"),
                )
            ),
        ),
        composition=CompositionMode.SYNTHESIS_SUBAGENT,
    )
    outcome = ExecutionOutcome(
        run_id=RUN_ID,
        state=RunState.COMPOSING,
        results=(
            _result(
                "compose",
                "Final synthesized answer.",
                evidence=(_evidence("Synthesis note", None, source="computed"),),
            ),
            _result(
                "notion",
                "Private Notion intermediate.",
                evidence=(_evidence("Notion page", "https://example.com/notion"),),
            ),
            _result(
                "github",
                "Private GitHub intermediate.",
                evidence=(_evidence("GitHub issue", "https://example.com/github"),),
            ),
        ),
    )

    candidate = DeterministicResultReducer().reduce(plan, outcome)

    assert "Final synthesized answer." in candidate.markdown
    assert "Private GitHub intermediate." not in candidate.markdown
    assert "Private Notion intermediate." not in candidate.markdown
    assert "Sources: [1], [2], [3]" in candidate.markdown
    assert candidate.evidence_links == (
        "https://example.com/github",
        "https://example.com/notion",
    )


def test_reducer_rejects_failed_duplicate_unknown_or_missing_results() -> None:
    required_plan = PreparedExecutionPlan(
        mode=RunMode.DELEGATE,
        steps=(
            PreparedExecutionStep(_task("required")),
            PreparedExecutionStep(
                _task("optional", depends_on=("required",)),
                requirement=StepRequirement.OPTIONAL,
            ),
        ),
    )
    required = _result("required", "Required result.")
    optional = _result("optional", "Optional result.")
    reducer = DeterministicResultReducer()

    invalid_outcomes = (
        (
            ExecutionOutcome(
                run_id=RUN_ID,
                state=RunState.FAILED,
                results=(required,),
                error_code=RunErrorCode.REQUIRED_STEP_FAILED,
            ),
            OrchestrationCompositionErrorCode.OUTCOME_NOT_COMPOSABLE,
        ),
        (
            ExecutionOutcome(
                run_id=RUN_ID,
                state=RunState.COMPOSING,
                results=(required, required),
            ),
            OrchestrationCompositionErrorCode.RESULT_SET_INVALID,
        ),
        (
            ExecutionOutcome(
                run_id=RUN_ID,
                state=RunState.COMPOSING,
                results=(required, _result("unknown", "Unknown result.")),
            ),
            OrchestrationCompositionErrorCode.RESULT_SET_INVALID,
        ),
        (
            ExecutionOutcome(
                run_id=RUN_ID,
                state=RunState.COMPOSING,
                results=(optional,),
            ),
            OrchestrationCompositionErrorCode.RESULT_SET_INVALID,
        ),
    )
    for outcome, code in invalid_outcomes:
        with pytest.raises(OrchestrationCompositionError) as captured:
            reducer.reduce(required_plan, outcome)
        assert captured.value.code is code


def test_reducer_rejects_mode_metadata_and_synthesis_mismatches() -> None:
    reducer = DeterministicResultReducer()
    direct_plan = PreparedExecutionPlan(mode=RunMode.DIRECT, direct_answer="Direct answer.")
    mismatched_direct = ExecutionOutcome(
        run_id=RUN_ID,
        state=RunState.COMPOSING,
        direct_answer="Different answer.",
    )
    with pytest.raises(OrchestrationCompositionError) as captured:
        reducer.reduce(direct_plan, mismatched_direct)
    assert captured.value.code is OrchestrationCompositionErrorCode.MODE_MISMATCH

    optional_plan = PreparedExecutionPlan(
        mode=RunMode.DELEGATE,
        steps=(
            PreparedExecutionStep(
                _task("optional"),
                requirement=StepRequirement.OPTIONAL,
            ),
        ),
    )
    optional_failure = ExecutionOutcome(
        run_id=RUN_ID,
        state=RunState.COMPOSING,
        results=(
            _result(
                "optional",
                "Failed.",
                status=AgentResultStatus.FAILED,
                error_code="source_failed",
            ),
        ),
    )
    with pytest.raises(OrchestrationCompositionError) as captured:
        reducer.reduce(optional_plan, optional_failure)
    assert captured.value.code is OrchestrationCompositionErrorCode.OUTCOME_METADATA_INVALID

    invalid_synthesis_plan = PreparedExecutionPlan(
        mode=RunMode.DELEGATE,
        steps=(PreparedExecutionStep(_task("compose", subagent="synthesis")),),
    )
    with pytest.raises(OrchestrationCompositionError) as captured:
        reducer.reduce(
            invalid_synthesis_plan,
            ExecutionOutcome(
                run_id=RUN_ID,
                state=RunState.COMPOSING,
                results=(_result("compose", "Unexpected synthesis."),),
            ),
        )
    assert captured.value.code is OrchestrationCompositionErrorCode.SYNTHESIS_RESULT_INVALID


def test_composer_returns_only_safe_output_for_direct_and_delegate_content() -> None:
    secret = "sk-private-output-secret-123456789"
    answer = f"token={secret} [unsafe](javascript:alert(1)) @channel /Users/example/private.txt"
    plan = PreparedExecutionPlan(mode=RunMode.DIRECT, direct_answer=answer)
    outcome = ExecutionOutcome(
        run_id=RUN_ID,
        state=RunState.COMPOSING,
        direct_answer=answer,
    )

    safe = OrchestrationOutputComposer(_output_guardrail()).compose(plan, outcome)

    assert secret not in safe.markdown
    assert "javascript:" not in safe.markdown
    assert "@channel" not in safe.markdown
    assert "/Users/" not in safe.markdown
    assert secret not in repr(safe)


def test_guardrail_rejection_and_unexpected_failure_never_expose_candidate() -> None:
    blocked_answer = "[bad]: javascript:alert(1)"
    blocked_plan = PreparedExecutionPlan(
        mode=RunMode.DIRECT,
        direct_answer=blocked_answer,
    )
    blocked_outcome = ExecutionOutcome(
        run_id=RUN_ID,
        state=RunState.COMPOSING,
        direct_answer=blocked_answer,
    )
    with pytest.raises(OutputGuardrailBlockedError) as captured:
        OrchestrationOutputComposer(_output_guardrail()).compose(
            blocked_plan,
            blocked_outcome,
        )
    assert blocked_answer not in f"{captured.value!s} {captured.value!r}"

    secret = "private-guardrail-failure"

    class ExplodingGuardrail:
        def sanitize(self, candidate: OutputCandidate) -> object:
            del candidate
            raise RuntimeError(secret)

    with pytest.raises(OrchestrationCompositionError) as unexpected:
        OrchestrationOutputComposer(  # type: ignore[arg-type]
            ExplodingGuardrail()
        ).compose(blocked_plan, blocked_outcome)
    assert unexpected.value.code is OrchestrationCompositionErrorCode.OUTPUT_GUARDRAIL_FAILED
    assert secret not in f"{unexpected.value!s} {unexpected.value!r}"
