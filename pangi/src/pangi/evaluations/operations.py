from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pangi.domain import EvalRun
from pangi.evaluations.case_loader import load_eval_cases
from pangi.evaluations.gate import collect_eval_gate_metadata
from pangi.evaluations.models import EvalCase, EvalRunResult
from pangi.evaluations.persistence import persist_eval_run
from pangi.evaluations.red_team import load_approved_red_team_cases
from pangi.evaluations.runner import run_eval_cases
from pangi.repository import JobRepository


DEFAULT_EVAL_CASES_PATH = Path(__file__).resolve().parents[3] / "evals" / "cases"


@dataclass(frozen=True)
class EvalSuiteRun:
    result: EvalRunResult
    persisted_run: EvalRun | None = None


async def run_eval_suite(
    *,
    repository: JobRepository | None = None,
    cases_path: Path | str = DEFAULT_EVAL_CASES_PATH,
    suite_name: str = "all",
    mode: str = "deterministic",
    fail_fast: bool = False,
    persist: bool = False,
    include_approved_red_team: bool = True,
) -> EvalSuiteRun:
    cases = list(load_eval_cases(Path(cases_path)))
    if repository is not None and include_approved_red_team:
        cases.extend(load_approved_red_team_cases(repository))

    result = await run_eval_cases(cases, fail_fast=fail_fast)
    persisted_run = None
    if persist:
        if repository is None:
            raise ValueError("repository is required when persist=True")
        metadata = collect_eval_gate_metadata(cases)
        persisted_run = persist_eval_run(
            repository,
            result,
            suite_name=suite_name,
            mode=mode,
            metadata=metadata,
        )
    return EvalSuiteRun(result=result, persisted_run=persisted_run)


def normalize_eval_cases_for_gate(cases: tuple[EvalCase, ...]) -> tuple[EvalCase, ...]:
    return tuple(sorted(cases, key=lambda case: (case.suite, case.id)))
