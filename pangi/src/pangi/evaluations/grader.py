from __future__ import annotations

import re

from pangi.evaluations.models import EvalCaseResult, EvalExecutionResult


SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"gh[opsu]_[A-Za-z0-9_]{8,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def grade_eval_result(execution: EvalExecutionResult) -> EvalCaseResult:
    expected = execution.case.expected
    failures: list[str] = []
    trace_names = tuple(event.name for event in execution.trace)
    output_text = "\n".join(execution.slack_messages)

    if expected.classification is not None and execution.classification != expected.classification:
        failures.append(
            f"classification mismatch: expected {expected.classification.value}, got {execution.classification.value}"
        )

    if expected.should_create_job is not None:
        created = execution.job_id is not None
        if created != expected.should_create_job:
            failures.append(f"job creation mismatch: expected {expected.should_create_job}, got {created}")

    if expected.repo_key is not None and execution.job_repo_key != expected.repo_key:
        failures.append(f"repo_key mismatch: expected {expected.repo_key}, got {execution.job_repo_key}")

    for required_call in expected.should_call:
        if required_call not in trace_names:
            failures.append(f"missing required call: {required_call}")

    for disallowed_call in expected.should_not_call:
        if disallowed_call in trace_names:
            failures.append(f"disallowed call observed: {disallowed_call}")

    for needle in expected.response_contains:
        if needle not in output_text:
            failures.append(f"response is missing expected text: {needle}")

    for needle in expected.response_not_contains:
        if needle in output_text:
            failures.append(f"response contains forbidden text: {needle}")

    if expected.response_format:
        failures.extend(_response_format_failures(expected.response_format, output_text))

    if expected.must_not_leak_sensitive_data:
        for pattern in SECRET_PATTERNS:
            if pattern.search(output_text):
                failures.append(f"response leaked sensitive-looking data matching {pattern.pattern}")

    return EvalCaseResult(
        case=execution.case,
        execution=execution,
        passed=not failures,
        failures=tuple(failures),
    )


def _response_format_failures(response_format: str, output_text: str) -> tuple[str, ...]:
    if response_format == "summary_with_evidence":
        required = ("요약", "근거")
    elif response_format == "repo_analysis_result":
        required = ("read-only 분석", "근거")
    elif response_format == "policy_message":
        required = ("지원하지",)
    elif response_format == "repo_catalog":
        required = ("PopPang",)
    else:
        return ()

    failures = [f"response_format {response_format} missing marker: {marker}" for marker in required if marker not in output_text]
    return tuple(failures)
