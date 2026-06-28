from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pangi.evaluations.models import EvalCase, eval_case_from_data


def load_eval_cases(path: str | Path) -> tuple[EvalCase, ...]:
    root = Path(path)
    if root.is_dir():
        files = tuple(sorted(root.glob("*.json")))
    else:
        files = (root,)

    cases: list[EvalCase] = []
    for file_path in files:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        cases.extend(_cases_from_document(data, default_suite=file_path.stem))
    return tuple(cases)


def _cases_from_document(data: Any, *, default_suite: str) -> tuple[EvalCase, ...]:
    if isinstance(data, list):
        return tuple(eval_case_from_data(item, default_suite=default_suite) for item in data)

    if not isinstance(data, dict):
        raise ValueError("Eval case file must contain an object or a list")

    suite = str(data.get("suite") or default_suite)
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("Eval case object must contain a cases list")

    return tuple(eval_case_from_data(item, default_suite=suite) for item in raw_cases)
