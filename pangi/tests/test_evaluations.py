import asyncio
from pathlib import Path

from pangi.evaluations.case_loader import load_eval_cases
from pangi.evaluations.grader import grade_eval_result
from pangi.evaluations.models import EvalCase, EvalExecutionResult, EvalTraceEvent, ExpectedBehavior
from pangi.evaluations.runner import format_json_report, format_markdown_report, run_eval_cases
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
