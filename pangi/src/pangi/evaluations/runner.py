from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Iterable

from pangi.domain import utc_now
from pangi.evaluations.grader import grade_eval_result
from pangi.evaluations.harness import execute_eval_case
from pangi.evaluations.models import EvalCase, EvalCaseResult, EvalRunResult


async def run_eval_cases(cases: Iterable[EvalCase], *, fail_fast: bool = False) -> EvalRunResult:
    started_at = utc_now()
    results: list[EvalCaseResult] = []
    for case in cases:
        execution = await execute_eval_case(case)
        result = grade_eval_result(execution)
        results.append(result)
        if fail_fast and not result.passed:
            break
    return EvalRunResult(results=tuple(results), started_at=started_at, finished_at=utc_now())


def run_eval_cases_sync(cases: Iterable[EvalCase], *, fail_fast: bool = False) -> EvalRunResult:
    return asyncio.run(run_eval_cases(cases, fail_fast=fail_fast))


def format_markdown_report(run: EvalRunResult) -> str:
    lines = [
        "# Pangi Eval Report",
        "",
        f"- total: {run.total_count}",
        f"- passed: {run.passed_count}",
        f"- failed: {run.failed_count}",
        "",
    ]
    for result in run.results:
        mark = "PASS" if result.passed else "FAIL"
        lines.append(f"## {mark} {result.case.id}")
        lines.append(f"- suite: {result.case.suite}")
        lines.append(f"- classification: {result.execution.classification.value}")
        lines.append(f"- job_id: {result.execution.job_id or '-'}")
        lines.append(f"- trace: {', '.join(event.name for event in result.execution.trace)}")
        if result.failures:
            lines.append("- failures:")
            lines.extend(f"  - {failure}" for failure in result.failures)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_json_report(run: EvalRunResult) -> str:
    payload = {
        "passed": run.passed,
        "total": run.total_count,
        "passed_count": run.passed_count,
        "failed_count": run.failed_count,
        "results": [
            {
                "case_id": result.case.id,
                "suite": result.case.suite,
                "passed": result.passed,
                "failures": list(result.failures),
                "classification": result.execution.classification.value,
                "job_id": result.execution.job_id,
                "trace": [asdict(event) for event in result.execution.trace],
            }
            for result in run.results
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
