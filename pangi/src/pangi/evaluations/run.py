from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pangi.evaluations.operations import run_eval_suite
from pangi.evaluations.red_team import generate_red_team_candidates
from pangi.evaluations.runner import format_json_report, format_markdown_report
from pangi.repository import get_job_repository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Pangi behavior and red-team eval suites.")
    parser.add_argument("--cases", default="evals/cases", help="Eval case JSON file or directory. Default: evals/cases")
    parser.add_argument("--json", action="store_true", help="Print a JSON report instead of Markdown.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failing case.")
    parser.add_argument("--persist", action="store_true", help="Persist run, case results, and trace events to SQLite.")
    parser.add_argument("--suite-name", default="all", help="Suite name stored with --persist. Default: all")
    parser.add_argument(
        "--include-approved-red-team",
        action="store_true",
        help="Include approved DB-backed red-team candidates in addition to JSON files.",
    )
    parser.add_argument(
        "--generate-red-team-candidates",
        action="store_true",
        help="Create deterministic draft red-team candidates in SQLite and exit.",
    )
    args = parser.parse_args(argv)

    repository = get_job_repository() if args.persist or args.include_approved_red_team or args.generate_red_team_candidates else None
    if args.generate_red_team_candidates:
        assert repository is not None
        candidates = generate_red_team_candidates(repository)
        print(f"created_or_updated_red_team_candidates={len(candidates)}")
        return 0

    suite_run = asyncio.run(
        run_eval_suite(
            repository=repository,
            cases_path=Path(args.cases),
            suite_name=args.suite_name,
            fail_fast=args.fail_fast,
            persist=args.persist,
            include_approved_red_team=args.include_approved_red_team,
        )
    )
    result = suite_run.result
    print(format_json_report(result) if args.json else format_markdown_report(result), end="")
    if suite_run.persisted_run is not None:
        print(f"\npersisted_eval_run_id={suite_run.persisted_run.id}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
