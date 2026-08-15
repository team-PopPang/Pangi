"""Run contracts and state machine unit tests."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from pangi.domain.auth import UserRole
from pangi.domain.runs import (
    AttachmentRef,
    EventVisibility,
    InvalidRunTransitionError,
    InvalidStepTransitionError,
    Principal,
    PrincipalChannel,
    Run,
    RunContractError,
    RunErrorCode,
    RunEvent,
    RunRequest,
    RunState,
    RunStep,
    StepRequirement,
    StepState,
    allowed_run_transitions,
    allowed_step_transitions,
    resolve_step_outcome,
    transition_run,
    transition_step,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)

RUN_EDGES = {
    (RunState.RECEIVED, RunState.BLOCKED),
    (RunState.RECEIVED, RunState.PLANNING),
    (RunState.RECEIVED, RunState.QUEUED),
    (RunState.BLOCKED, RunState.COMPLETED),
    (RunState.PLANNING, RunState.QUEUED),
    (RunState.PLANNING, RunState.FAILED),
    (RunState.QUEUED, RunState.RUNNING),
    (RunState.QUEUED, RunState.CANCELLED),
    (RunState.RUNNING, RunState.COMPOSING),
    (RunState.RUNNING, RunState.FAILED),
    (RunState.RUNNING, RunState.CANCELLED),
    (RunState.RUNNING, RunState.INTERRUPTED),
    (RunState.INTERRUPTED, RunState.QUEUED),
    (RunState.INTERRUPTED, RunState.FAILED),
    (RunState.COMPOSING, RunState.COMPLETED),
    (RunState.COMPOSING, RunState.FAILED),
}

STEP_EDGES = {
    (StepState.QUEUED, StepState.RUNNING),
    (StepState.QUEUED, StepState.CANCELLED),
    (StepState.RUNNING, StepState.COMPLETED),
    (StepState.RUNNING, StepState.FAILED),
    (StepState.RUNNING, StepState.CANCELLED),
    (StepState.RUNNING, StepState.INTERRUPTED),
    (StepState.INTERRUPTED, StepState.QUEUED),
    (StepState.INTERRUPTED, StepState.FAILED),
}


def _request() -> RunRequest:
    return RunRequest(
        request_id="request-identifier-1",
        principal=Principal(
            "principal-user-0001",
            UserRole.MEMBER,
            PrincipalChannel.DASHBOARD,
        ),
        text="이번 주 열린 이슈를 요약해줘",
        idempotency_key="request-once-1",
        created_at=NOW,
        attachments=(
            AttachmentRef(
                "attachment-ref-0001",
                display_name="issues.csv",
                media_type="text/csv",
                size_bytes=120,
                fingerprint="a" * 64,
            ),
        ),
    )


def _run(state: RunState) -> Run:
    finished_at = (
        NOW if state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED} else None
    )
    return Run(
        id="run-identifier-0001",
        request=_request(),
        state=state,
        updated_at=NOW,
        finished_at=finished_at,
    )


def _step(
    state: StepState,
    *,
    node_id: str = "collect",
    requirement: StepRequirement = StepRequirement.REQUIRED,
) -> RunStep:
    finished_at = (
        NOW if state in {StepState.COMPLETED, StepState.FAILED, StepState.CANCELLED} else None
    )
    return RunStep(
        id=f"step-{node_id}-identifier",
        run_id="run-identifier-0001",
        node_id=node_id,
        type="subagent",
        state=state,
        requirement=requirement,
        idempotent=True,
        attempt=1,
        created_at=NOW,
        updated_at=NOW,
        finished_at=finished_at,
    )


def test_run_state_machine_accepts_only_the_declared_edges() -> None:
    at = NOW + timedelta(seconds=1)
    for current in RunState:
        expected = frozenset(target for source, target in RUN_EDGES if source is current)
        assert allowed_run_transitions(current) == expected
        for target in RunState:
            run = _run(current)
            if (current, target) in RUN_EDGES:
                changed = transition_run(run, target, at=at)
                assert changed.state is target
                assert changed.revision == 1
                assert changed.updated_at == at
                if target is RunState.RUNNING:
                    assert changed.started_at == at
                if target in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
                    assert changed.finished_at == at
            else:
                with pytest.raises(InvalidRunTransitionError) as captured:
                    transition_run(run, target, at=at)
                assert captured.value.code is RunErrorCode.INVALID_RUN_STATE_TRANSITION


def test_step_state_machine_accepts_only_the_declared_edges() -> None:
    at = NOW + timedelta(seconds=1)
    for current in StepState:
        expected = frozenset(target for source, target in STEP_EDGES if source is current)
        assert allowed_step_transitions(current) == expected
        for target in StepState:
            step = _step(current)
            if (current, target) in STEP_EDGES:
                changed = transition_step(step, target, at=at)
                assert changed.state is target
                assert changed.updated_at == at
                if target is StepState.RUNNING:
                    assert changed.started_at == at
                if target in {StepState.COMPLETED, StepState.FAILED, StepState.CANCELLED}:
                    assert changed.finished_at == at
            else:
                with pytest.raises(InvalidStepTransitionError) as captured:
                    transition_step(step, target, at=at)
                assert captured.value.code is RunErrorCode.INVALID_STEP_STATE_TRANSITION


def test_required_and_optional_step_failures_have_distinct_run_outcomes() -> None:
    completed = _step(StepState.COMPLETED)
    optional_failure = _step(
        StepState.FAILED,
        node_id="enrichment",
        requirement=StepRequirement.OPTIONAL,
    )
    required_failure = _step(StepState.FAILED, node_id="required-source")

    partial = resolve_step_outcome((completed, optional_failure))
    failed = resolve_step_outcome((completed, optional_failure, required_failure))

    assert partial.state is RunState.COMPLETED
    assert partial.error_code is RunErrorCode.OPTIONAL_STEP_FAILED
    assert partial.warnings == ("optional step failed: enrichment",)
    assert failed.state is RunState.FAILED
    assert failed.error_code is RunErrorCode.REQUIRED_STEP_FAILED
    assert failed.warnings == ()
    with pytest.raises(RunContractError, match="terminal"):
        resolve_step_outcome((_step(StepState.RUNNING),))


def test_run_contracts_require_safe_normalized_values() -> None:
    request = _request()
    assert request.created_at.tzinfo is UTC
    assert request.attachments[0].reference == "attachment-ref-0001"
    assert not hasattr(request.attachments[0], "body")
    assert not hasattr(request.attachments[0], "url")
    string_principal = Principal(
        "principal-user-0002",
        "member",  # type: ignore[arg-type]
        "api",  # type: ignore[arg-type]
    )
    assert string_principal.role is UserRole.MEMBER
    assert string_principal.channel is PrincipalChannel.API
    string_run = Run(
        id="run-identifier-0002",
        request=request,
        state="queued",  # type: ignore[arg-type]
        mode="direct",  # type: ignore[arg-type]
        updated_at=NOW,
    )
    assert string_run.state is RunState.QUEUED
    assert string_run.mode is not None and string_run.mode.value == "direct"
    string_step = RunStep(
        id="step-identifier-002",
        run_id=string_run.id,
        node_id="collect",
        type="subagent",
        state="queued",  # type: ignore[arg-type]
        requirement="optional",  # type: ignore[arg-type]
        idempotent=True,
        attempt=1,
        created_at=NOW,
        updated_at=NOW,
    )
    assert string_step.state is StepState.QUEUED
    assert string_step.requirement is StepRequirement.OPTIONAL

    event_attributes: dict[str, object] = {
        "request_fingerprint": "a" * 64,
        "summary": {"source_count": 1},
    }
    event = RunEvent(
        run_id="run-identifier-0001",
        index=1,
        type="run.received",
        visibility="public",  # type: ignore[arg-type]
        created_at=NOW,
        attributes=event_attributes,
    )
    assert event.visibility is EventVisibility.PUBLIC
    assert event.attributes["request_fingerprint"] == "a" * 64
    event_attributes["summary"] = {"provider_prompt": "late mutation"}
    nested_summary = event.attributes["summary"]
    assert isinstance(nested_summary, Mapping)
    assert nested_summary == {"source_count": 1}
    with pytest.raises(TypeError):
        event.attributes["new"] = "not mutable"  # type: ignore[index]
    with pytest.raises(TypeError):
        nested_summary["new"] = "not mutable"  # type: ignore[index]

    with pytest.raises(RunContractError, match="timezone-aware"):
        RunRequest(
            request_id="request-identifier-1",
            principal=request.principal,
            text="request",
            idempotency_key="request-once-1",
            created_at=datetime(2030, 1, 1),
        )
    with pytest.raises(RunContractError, match="forbidden"):
        RunEvent(
            run_id="run-identifier-0001",
            index=1,
            type="run.received",
            visibility=EventVisibility.INTERNAL,
            created_at=NOW,
            attributes={"nested": {"provider_prompt": "must not persist"}},
        )
    with pytest.raises(RunContractError, match="JSON-compatible"):
        RunEvent(
            run_id="run-identifier-0001",
            index=1,
            type="run.received",
            visibility=EventVisibility.INTERNAL,
            created_at=NOW,
            attributes={"created_at": NOW},
        )
    with pytest.raises(RunContractError, match="opaque"):
        AttachmentRef("https://example.com/file?token=secret")
