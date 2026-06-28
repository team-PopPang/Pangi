from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pangi.usecase.request_decision import ClassifiedRequest, RequestClassification


@dataclass(frozen=True)
class ExpectedBehavior:
    classification: RequestClassification | None = None
    should_create_job: bool | None = None
    repo_key: str | None = None
    should_call: tuple[str, ...] = ()
    should_not_call: tuple[str, ...] = ()
    response_contains: tuple[str, ...] = ()
    response_not_contains: tuple[str, ...] = ()
    response_format: str | None = None
    must_not_leak_sensitive_data: bool = True


@dataclass(frozen=True)
class EvalCase:
    id: str
    name: str
    input: str
    suite: str
    mode: str = "deterministic"
    tags: tuple[str, ...] = ()
    allowed_repo_keys: tuple[str, ...] = ("PopPang-iOS", "PopPang-BE")
    local_repo_keys: tuple[str, ...] = ("PopPang-iOS",)
    thread_context: str = ""
    expected: ExpectedBehavior = field(default_factory=ExpectedBehavior)
    hostile_decision: ClassifiedRequest | None = None
    notion_context_markdown: str | None = None
    git_context_markdown: str | None = None
    codex_stdout: str | None = None


@dataclass(frozen=True)
class EvalTraceEvent:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalExecutionResult:
    case: EvalCase
    classification: RequestClassification
    job_id: str | None
    job_repo_key: str | None
    trace: tuple[EvalTraceEvent, ...]
    slack_messages: tuple[str, ...]


@dataclass(frozen=True)
class EvalCaseResult:
    case: EvalCase
    execution: EvalExecutionResult
    passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True)
class EvalRunResult:
    results: tuple[EvalCaseResult, ...]
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def total_count(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def failed_count(self) -> int:
        return self.total_count - self.passed_count


def classified_request_from_data(data: dict[str, Any] | None) -> ClassifiedRequest | None:
    if not data:
        return None
    return ClassifiedRequest(
        kind=RequestClassification(data["classification"]),
        should_create_job=bool(data.get("should_create_job", False)),
        repo_key=data.get("repo_key"),
        reply_text=data.get("reply_text"),
        reason=data.get("reason"),
    )


def expected_behavior_from_data(data: dict[str, Any] | None) -> ExpectedBehavior:
    data = data or {}
    classification = data.get("classification")
    return ExpectedBehavior(
        classification=RequestClassification(classification) if classification else None,
        should_create_job=data.get("should_create_job"),
        repo_key=data.get("repo_key"),
        should_call=tuple(data.get("should_call", ())),
        should_not_call=tuple(data.get("should_not_call", ())),
        response_contains=tuple(data.get("response_contains", ())),
        response_not_contains=tuple(data.get("response_not_contains", ())),
        response_format=data.get("response_format"),
        must_not_leak_sensitive_data=bool(data.get("must_not_leak_sensitive_data", True)),
    )


def eval_case_from_data(data: dict[str, Any], *, default_suite: str) -> EvalCase:
    return EvalCase(
        id=data["id"],
        name=data.get("name") or data["id"],
        input=data["input"],
        suite=data.get("suite") or default_suite,
        mode=data.get("mode", "deterministic"),
        tags=tuple(data.get("tags", ())),
        allowed_repo_keys=tuple(data.get("allowed_repo_keys", ("PopPang-iOS", "PopPang-BE"))),
        local_repo_keys=tuple(data.get("local_repo_keys", ("PopPang-iOS",))),
        thread_context=data.get("thread_context", ""),
        expected=expected_behavior_from_data(data.get("expected_behavior") or data.get("expected")),
        hostile_decision=classified_request_from_data(data.get("hostile_decision")),
        notion_context_markdown=data.get("notion_context_markdown"),
        git_context_markdown=data.get("git_context_markdown"),
        codex_stdout=data.get("codex_stdout"),
    )
