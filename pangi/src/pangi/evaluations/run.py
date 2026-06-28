from __future__ import annotations

import argparse
from pathlib import Path

from pangi.evaluations.case_loader import load_eval_cases
from pangi.evaluations.runner import format_json_report, format_markdown_report, run_eval_cases_sync


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Pangi behavior and red-team eval suites.")
    parser.add_argument("--cases", default="evals/cases", help="Eval case JSON file or directory. Default: evals/cases")
    parser.add_argument("--json", action="store_true", help="Print a JSON report instead of Markdown.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failing case.")
    args = parser.parse_args(argv)

    cases = load_eval_cases(Path(args.cases))
    result = run_eval_cases_sync(cases, fail_fast=args.fail_fast)
    print(format_json_report(result) if args.json else format_markdown_report(result), end="")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
