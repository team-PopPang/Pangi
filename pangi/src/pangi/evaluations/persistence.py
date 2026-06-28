from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from pangi.domain import EvalCaseStatus, EvalRun, EvalRunStatus, utc_now
from pangi.evaluations.gate import EvalGateMetadata
from pangi.evaluations.models import EvalCase, EvalRunResult
from pangi.repository import JobRepository


def persist_eval_run(
    repository: JobRepository,
    result: EvalRunResult,
    *,
    suite_name: str,
    mode: str,
    metadata: EvalGateMetadata,
) -> EvalRun:
    started_at = result.started_at or utc_now()
    finished_at = result.finished_at or utc_now()
    eval_run = repository.create_eval_run(
        suite=suite_name,
        mode=mode,
        status=EvalRunStatus.SUCCEEDED if result.passed else EvalRunStatus.FAILED,
        total_count=result.total_count,
        passed_count=result.passed_count,
        failed_count=result.failed_count,
        prompt_fingerprint=metadata.prompt_fingerprint,
        model_fingerprint=metadata.model_fingerprint,
        provider_fingerprint=metadata.provider_fingerprint,
        started_at=started_at,
        finished_at=finished_at,
    )

    for case_result in result.results:
        case = case_result.case
        repository.upsert_eval_case(
            suite=case.suite,
            case_id=case.id,
            name=case.name,
            tags=case.tags,
            case_json=eval_case_to_json(case),
        )
        record = repository.append_eval_case_result(
            eval_run_id=eval_run.id,
            suite=case.suite,
            case_id=case.id,
            name=case.name,
            status=EvalCaseStatus.PASSED if case_result.passed else EvalCaseStatus.FAILED,
            classification=case_result.execution.classification.value,
            job_id=case_result.execution.job_id,
            job_repo_key=case_result.execution.job_repo_key,
            failures=case_result.failures,
            slack_messages=case_result.execution.slack_messages,
        )
        for index, event in enumerate(case_result.execution.trace):
            repository.append_eval_trace_event(
                eval_case_result_id=record.id,
                event_index=index,
                name=event.name,
                attributes=_jsonable(event.attributes),
            )

    return eval_run


def eval_case_to_json(case: EvalCase) -> dict[str, object]:
    return _jsonable(asdict(case))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
