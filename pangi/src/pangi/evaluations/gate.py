from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pangi.evaluations.models import EvalCase


MODEL_ENV_NAMES = (
    "PANGI_CHAT_MODEL",
    "PANGI_ORCHESTRATOR_MODEL",
    "PANGI_ANALYSIS_MODEL",
)


@dataclass(frozen=True)
class EvalGateMetadata:
    prompt_fingerprint: str
    model_fingerprint: str
    provider_fingerprint: str


def collect_eval_gate_metadata(cases: Iterable[EvalCase]) -> EvalGateMetadata:
    case_tuple = tuple(cases)
    return EvalGateMetadata(
        prompt_fingerprint=_fingerprint_prompt_files(),
        model_fingerprint=_fingerprint_model_env(),
        provider_fingerprint=_fingerprint_provider_contract(case_tuple),
    )


def _fingerprint_prompt_files() -> str:
    prompt_root = Path(__file__).resolve().parents[1] / "prompts"
    parts: list[str] = []
    for prompt_path in sorted(prompt_root.glob("*.md")):
        parts.append(prompt_path.name)
        parts.append(prompt_path.read_text(encoding="utf-8"))
    return _sha256_text("\n".join(parts))


def _fingerprint_model_env() -> str:
    parts = [f"{name}={os.environ.get(name, '').strip()}" for name in MODEL_ENV_NAMES]
    return _sha256_text("\n".join(parts))


def _fingerprint_provider_contract(cases: tuple[EvalCase, ...]) -> str:
    parts: list[str] = []
    for case in sorted(cases, key=lambda item: (item.suite, item.id)):
        expected = case.expected
        parts.extend(
            (
                case.suite,
                case.id,
                case.mode,
                ",".join(case.tags),
                ",".join(expected.should_call),
                ",".join(expected.should_not_call),
                expected.response_format or "",
            )
        )
    return _sha256_text("\n".join(parts))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
