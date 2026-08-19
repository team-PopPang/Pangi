"""Root orchestration contract unit tests."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest

import pangi
from pangi.application.contracts.orchestration import (
    AgentResult,
    AgentResultStatus,
    DelegatedTask,
    Evidence,
    EvidenceSourceType,
    OrchestratorDecision,
)
from pangi.domain.runs import RunMode


def test_agent_result_is_public_deeply_immutable_and_secret_safe() -> None:
    title_secret = "private evidence title"
    uri_secret = "https://example.test/private?token=secret"
    excerpt_secret = "private evidence excerpt"
    summary_secret = "private result summary"
    warning_secret = "private warning"
    fact: dict[str, object] = {"nested": {"count": 1}, "items": ["first"]}
    evidence = Evidence(
        EvidenceSourceType.MCP,
        "github",
        title_secret,
        uri_secret,
        excerpt_secret,
    )
    result = AgentResult(
        task_id="collect-issues",
        status=AgentResultStatus.PARTIAL,
        summary_markdown=summary_secret,
        evidence=(evidence,),
        facts=(fact,),
        warnings=(warning_secret,),
        error_code="source_timeout",
    )

    assert pangi.AgentResult is AgentResult
    assert pangi.Evidence is Evidence
    assert result.facts[0]["items"] == ("first",)
    fact["nested"] = {"count": 999}
    nested = result.facts[0]["nested"]
    assert isinstance(nested, Mapping)
    assert nested["count"] == 1
    with pytest.raises(TypeError):
        result.facts[0]["new"] = "mutation"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        result.status = AgentResultStatus.SUCCEEDED  # type: ignore[misc]

    representation = repr(result)
    for secret in (
        title_secret,
        uri_secret,
        excerpt_secret,
        summary_secret,
        warning_secret,
    ):
        assert secret not in representation


def test_agent_result_status_contract_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="requires error_code"):
        AgentResult(
            task_id="collect-issues",
            status=AgentResultStatus.FAILED,
            summary_markdown="Source failed.",
        )
    with pytest.raises(ValueError, match="cannot contain error_code"):
        AgentResult(
            task_id="collect-issues",
            status=AgentResultStatus.SUCCEEDED,
            summary_markdown="Source succeeded.",
            error_code="unexpected_error",
        )
    with pytest.raises(ValueError, match="JSON-compatible"):
        AgentResult(
            task_id="collect-issues",
            status=AgentResultStatus.SUCCEEDED,
            summary_markdown="Source succeeded.",
            facts=({"unsupported": object()},),
        )


def test_orchestrator_decision_hides_model_generated_content() -> None:
    answer_secret = "private direct answer"
    user_message_secret = "private progress message"
    objective_secret = "private delegated objective"
    task = DelegatedTask(
        id="collect-issues",
        subagent="github-research",
        objective=objective_secret,
    )
    direct = OrchestratorDecision(
        mode=RunMode.DIRECT,
        direct_answer=answer_secret,
        user_message=user_message_secret,
    )

    assert answer_secret not in repr(direct)
    assert user_message_secret not in repr(direct)
    assert objective_secret not in repr(task)
    with pytest.raises(ValueError, match="immutable tuple"):
        OrchestratorDecision(
            mode=RunMode.DELEGATE,
            tasks=[task],  # type: ignore[arg-type]
        )
