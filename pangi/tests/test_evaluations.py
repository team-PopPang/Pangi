import asyncio
from pathlib import Path

from pangi.domain import EvalRedTeamCandidateStatus
from pangi.evaluations.case_loader import load_eval_cases
from pangi.evaluations.gate import collect_eval_gate_metadata
from pangi.evaluations.grader import grade_eval_result
from pangi.evaluations.models import EvalCase, EvalExecutionResult, EvalTraceEvent, ExpectedBehavior
from pangi.evaluations.persistence import persist_eval_run
from pangi.evaluations.red_team import generate_red_team_candidates, load_approved_red_team_cases
from pangi.evaluations.runner import format_json_report, format_markdown_report, run_eval_cases
from pangi.repository import SQLiteJobRepository
from pangi.usecase.request_decision import RequestClassification


def test_bundled_eval_cases_pass():
    async def scenario():
        cases = load_eval_cases(Path(__file__).resolve().parents[1] / "evals" / "cases")

        result = await run_eval_cases(cases)

        assert result.passed is True
        assert result.total_count >= 10
        report = format_markdown_report(result)
        assert "Pangi Eval Report" in report
        assert "FAIL" not in report

    asyncio.run(scenario())


def test_eval_grader_detects_disallowed_call():
    case = EvalCase(
        id="detect-disallowed-call",
        name="detect disallowed call",
        suite="unit",
        input="PR 만들어줘",
        expected=ExpectedBehavior(
            classification=RequestClassification.UNSUPPORTED,
            should_create_job=False,
            should_not_call=("codex.run_read_only",),
        ),
    )
    execution = EvalExecutionResult(
        case=case,
        classification=RequestClassification.UNSUPPORTED,
        job_id=None,
        job_repo_key=None,
        trace=(EvalTraceEvent(name="codex.run_read_only"),),
        slack_messages=("현재 MVP에서는 지원하지 않습니다.",),
    )

    result = grade_eval_result(execution)

    assert result.passed is False
    assert result.failures == ("disallowed call observed: codex.run_read_only",)


def test_eval_json_report_contains_trace_names():
    async def scenario():
        cases = load_eval_cases(Path(__file__).resolve().parents[1] / "evals" / "cases" / "core_behavior.json")
        result = await run_eval_cases(cases[:1])

        report = format_json_report(result)

        assert '"passed": true' in report
        assert "input_guardrail.route" in report

    asyncio.run(scenario())


def test_eval_persistence_stores_run_results_and_traces(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        cases = load_eval_cases(Path(__file__).resolve().parents[1] / "evals" / "cases" / "core_behavior.json")
        result = await run_eval_cases(cases[:2])
        metadata = collect_eval_gate_metadata(cases[:2])

        persisted = persist_eval_run(
            repository,
            result,
            suite_name="unit",
            mode="deterministic",
            metadata=metadata,
        )

        assert persisted.total_count == 2
        assert persisted.prompt_fingerprint
        assert repository.list_eval_runs(limit=10) == [persisted]
        assert len(repository.list_eval_cases(limit=10)) == 2
        stored_results = repository.list_eval_case_results(eval_run_id=persisted.id, limit=10)
        assert len(stored_results) == 2
        assert repository.list_eval_trace_events(eval_case_result_id=stored_results[0].id, limit=20)

    asyncio.run(scenario())


def test_red_team_candidates_can_be_approved_and_loaded(tmp_path):
    repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
    candidates = generate_red_team_candidates(repository)

    approved = repository.set_eval_red_team_candidate_status(
        candidates[0].id,
        status=EvalRedTeamCandidateStatus.APPROVED,
    )
    approved_cases = load_approved_red_team_cases(repository)

    assert approved.status == EvalRedTeamCandidateStatus.APPROVED
    assert len(candidates) >= 3
    assert len(approved_cases) == 1
    assert approved_cases[0].id == candidates[0].case_id
