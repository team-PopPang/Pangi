"""Deterministic Root context and Decision parser tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from pangi.adapters.outbound.model_providers.json_schema import JsonSchemaOutputValidator
from pangi.application.contracts.guardrails import GuardedRunRequest, GuardrailDecision
from pangi.application.contracts.orchestration import (
    CompositionMode,
    OrchestratorLimits,
)
from pangi.application.contracts.root_orchestration import (
    RootCatalogSnapshot,
    RootOrchestrationRequest,
    RootOrchestratorPolicy,
    RootSkillDescriptor,
    RootSubagentDescriptor,
)
from pangi.application.services.root_context import (
    ROOT_DECISION_SCHEMA_NAME,
    RootContextBuilder,
    RootDecisionParseError,
    RootDecisionParser,
    root_decision_output_schema,
)
from pangi.domain.auth import UserRole
from pangi.domain.guardrails import GuardrailOutcome, GuardrailStage, TrustLevel
from pangi.domain.model_routing import DataClass, ModelMessageRole, ModelPurpose
from pangi.domain.runs import AttachmentRef, Principal, PrincipalChannel, RunMode, RunRequest

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _guardrail_decision() -> GuardrailDecision:
    return GuardrailDecision(
        trust_level=TrustLevel.UNTRUSTED,
        stage=GuardrailStage.COMPLETE,
        outcome=GuardrailOutcome.ALLOWED,
        policy_version="input-v1",
        policy_fingerprint="a" * 64,
        unicode_policy_version="unicode-v1",
        text_bytes=20,
    )


def _request(
    *,
    text: str = "이번 주 열린 이슈를 요약해줘",
    explicit_skill: str | None = None,
    schedule_id: str | None = None,
) -> RootOrchestrationRequest:
    run_request = RunRequest(
        request_id="request-root-0001",
        principal=Principal(
            "principal-secret-0001",
            UserRole.MEMBER,
            PrincipalChannel.DASHBOARD,
        ),
        text=text,
        idempotency_key="idempotency-secret-0001",
        created_at=NOW,
        thread_key="thread-secret-0001",
        explicit_skill=explicit_skill,
        schedule_id=schedule_id,
        attachments=(
            AttachmentRef(
                "attachment-secret-reference",
                display_name="issues.csv",
                media_type="text/csv",
                size_bytes=120,
                fingerprint="b" * 64,
            ),
        ),
    )
    return RootOrchestrationRequest(
        run_id="run-root-context-0001",
        guarded_request=GuardedRunRequest(run_request, _guardrail_decision()),
        data_classes=frozenset({DataClass.INTERNAL}),
    )


def _catalog() -> RootCatalogSnapshot:
    return RootCatalogSnapshot(
        version="catalog-v1",
        subagents=(
            RootSubagentDescriptor("notion-research", "Search approved Notion pages."),
            RootSubagentDescriptor("github-research", "Search approved GitHub data."),
        ),
        skills=(
            RootSkillDescriptor(
                "weekly-summary",
                "Build the approved weekly summary.",
                ("weekly report", "Friday summary"),
            ),
        ),
        connection_names=("notion-primary", "github-primary"),
    )


def _policy() -> RootOrchestratorPolicy:
    return RootOrchestratorPolicy(
        profile="root-default",
        prompt_version="root-orchestration-v1",
        limits=OrchestratorLimits(max_tasks=3, run_timeout_seconds=180),
    )


def _decision_payload(mode: RunMode) -> dict[str, object]:
    payload: dict[str, object] = {
        "composition": CompositionMode.DETERMINISTIC.value,
        "direct_answer": None,
        "mode": mode.value,
        "skill_name": None,
        "tasks": [],
        "user_message": None,
    }
    if mode is RunMode.DIRECT:
        payload["direct_answer"] = "안녕하세요."
    elif mode is RunMode.SKILL:
        payload["skill_name"] = "weekly-summary"
    else:
        payload["tasks"] = [
            {
                "allowed_tool_hints": ["github.search_issues"],
                "connection_hints": ["github-primary"],
                "depends_on": [],
                "id": "collect-issues",
                "objective": "Collect the current issues.",
                "subagent": "github-research",
                "timeout_seconds": 60,
            }
        ]
    return payload


def test_catalog_snapshot_is_deterministic_and_secret_safe() -> None:
    catalog = _catalog()
    reordered = RootCatalogSnapshot(
        version="catalog-v1",
        subagents=tuple(reversed(catalog.subagents)),
        skills=catalog.skills,
        connection_names=tuple(reversed(catalog.connection_names)),
    )

    assert tuple(item.name for item in catalog.subagents) == (
        "github-research",
        "notion-research",
    )
    assert catalog.connection_names == ("github-primary", "notion-primary")
    assert catalog.fingerprint == reordered.fingerprint
    assert catalog.validation_catalog.available_subagents == frozenset(
        {"github-research", "notion-research"}
    )
    rendered = repr(catalog)
    assert "Search approved GitHub data" not in rendered
    assert "weekly report" not in rendered

    with pytest.raises(ValueError, match="subagent names"):
        RootCatalogSnapshot(
            version="catalog-v1",
            subagents=(catalog.subagents[0], catalog.subagents[0]),
        )
    with pytest.raises(ValueError, match="connection names"):
        RootCatalogSnapshot(
            version="catalog-v1",
            connection_names=("duplicate", "duplicate"),
        )
    with pytest.raises(ValueError, match="at most 100"):
        RootCatalogSnapshot(
            version="catalog-v1",
            connection_names=tuple(f"connection-{index}" for index in range(101)),
        )


def test_root_context_is_canonical_minimal_and_secret_safe() -> None:
    request_secret = "sk-request-secret-123456789"
    request = _request(text=f"Summarize {request_secret}")
    builder = RootContextBuilder(_policy())

    first = builder.build(
        request,
        catalog=_catalog(),
        logical_call_id="root-orchestration:run-root-context-0001",
    )
    second = builder.build(
        request,
        catalog=_catalog(),
        logical_call_id="root-orchestration:run-root-context-0001",
    )

    assert first == second
    assert first.purpose is ModelPurpose.ORCHESTRATION
    assert first.profile == "root-default"
    assert first.output_schema.name == ROOT_DECISION_SCHEMA_NAME
    assert tuple(source.role for source in first.sources) == (
        ModelMessageRole.SYSTEM,
        ModelMessageRole.USER,
    )
    assert tuple(source.source_kind for source in first.sources) == ("policy", "channel")
    assert first.sources[0].raw_content is False
    assert first.sources[1].raw_content is True

    system_data = json.loads(first.sources[0].canonical_data_json or "")
    user_data = json.loads(first.sources[1].canonical_data_json or "")
    assert system_data["catalog"]["connection_names"] == [
        "github-primary",
        "notion-primary",
    ]
    assert "tools" not in system_data["catalog"]
    assert "memory" not in system_data["catalog"]
    assert user_data == {
        "attachments": [
            {
                "display_name": "issues.csv",
                "media_type": "text/csv",
                "size_bytes": 120,
            }
        ],
        "channel": "dashboard",
        "text": f"Summarize {request_secret}",
    }
    serialized_sources = " ".join(source.canonical_data_json or "" for source in first.sources)
    for excluded in (
        "principal-secret-0001",
        "idempotency-secret-0001",
        "thread-secret-0001",
        "attachment-secret-reference",
        "request-root-0001",
        "b" * 64,
    ):
        assert excluded not in serialized_sources
    assert request_secret not in repr(request)
    assert request_secret not in repr(first)


def test_decision_schema_and_parser_share_the_same_contract() -> None:
    schema = root_decision_output_schema()
    validator = JsonSchemaOutputValidator()
    parser = RootDecisionParser()

    for mode in RunMode:
        canonical = json.dumps(
            _decision_payload(mode),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert validator.is_valid(schema=schema, canonical_output_json=canonical)
        decision = parser.parse(canonical)
        assert decision.mode is mode

    extra = _decision_payload(RunMode.DIRECT)
    extra["unexpected"] = "field"
    assert not validator.is_valid(
        schema=schema,
        canonical_output_json=json.dumps(extra),
    )


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.update({"secret-extra-field": "private"}),
        lambda value: value.update({"tasks": "not-an-array"}),
        lambda value: value.update({"mode": "unknown-mode"}),
        lambda value: value.update(
            {
                "tasks": [
                    {
                        **_decision_payload(RunMode.DELEGATE)["tasks"][0],  # type: ignore[index]
                        "timeout_seconds": True,
                    }
                ]
            }
        ),
    ),
)
def test_decision_parser_rejects_invalid_output_without_disclosing_it(
    mutate: object,
) -> None:
    payload = _decision_payload(RunMode.DELEGATE)
    assert callable(mutate)
    mutate(payload)
    output_secret = "private-model-output"
    payload["user_message"] = output_secret

    with pytest.raises(RootDecisionParseError) as captured:
        RootDecisionParser().parse(json.dumps(payload))

    assert captured.value.code == "root_decision_invalid_output"
    assert output_secret not in str(captured.value)
    assert output_secret not in repr(captured.value)


def test_root_request_requires_an_allowed_guardrail_and_explicit_data_classes() -> None:
    allowed = _request()
    assert allowed.data_classes == frozenset({DataClass.INTERNAL})

    blocked = GuardrailDecision(
        trust_level=TrustLevel.UNTRUSTED,
        stage=GuardrailStage.TEXT,
        outcome=GuardrailOutcome.BLOCKED,
        policy_version="input-v1",
        policy_fingerprint="a" * 64,
        unicode_policy_version="unicode-v1",
        error_code="text_bytes_exceeded",  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="allowed input decision"):
        RootOrchestrationRequest(
            run_id=allowed.run_id,
            guarded_request=GuardedRunRequest(allowed.guarded_request.request, blocked),
            data_classes=frozenset({DataClass.INTERNAL}),
        )
    with pytest.raises(ValueError, match="non-empty"):
        RootOrchestrationRequest(
            run_id=allowed.run_id,
            guarded_request=allowed.guarded_request,
            data_classes=frozenset(),
        )
