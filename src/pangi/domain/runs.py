"""Framework-free Run contracts and state transition policies."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType

from pangi.domain.auth import UserRole
from pangi.domain.telemetry import RUN_EVENT_FORBIDDEN_ATTRIBUTE_KEYS

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_IDEMPOTENCY_KEY = re.compile(r"^[!-~]{1,255}$")
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class PrincipalChannel(StrEnum):
    SLACK = "slack"
    API = "api"
    DASHBOARD = "dashboard"
    SCHEDULER = "scheduler"
    EVAL = "eval"


class RunMode(StrEnum):
    DIRECT = "direct"
    DELEGATE = "delegate"
    SKILL = "skill"


class RunState(StrEnum):
    RECEIVED = "received"
    BLOCKED = "blocked"
    PLANNING = "planning"
    QUEUED = "queued"
    RUNNING = "running"
    COMPOSING = "composing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class StepState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class StepRequirement(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"


class EventVisibility(StrEnum):
    PUBLIC = "public"
    ADMIN = "admin"
    INTERNAL = "internal"


class RunErrorCode(StrEnum):
    INVALID_RUN_STATE_TRANSITION = "invalid_run_state_transition"
    INVALID_STEP_STATE_TRANSITION = "invalid_step_state_transition"
    REQUIRED_STEP_FAILED = "required_step_failed"
    OPTIONAL_STEP_FAILED = "optional_step_failed"
    NON_IDEMPOTENT_RECOVERY = "non_idempotent_recovery"
    COMPOSITION_INTERRUPTED = "composition_interrupted"


class RunContractError(ValueError):
    """A Run value violates a stable public contract."""


class InvalidRunTransitionError(RuntimeError):
    code = RunErrorCode.INVALID_RUN_STATE_TRANSITION

    def __init__(self, current: RunState, target: RunState) -> None:
        super().__init__(f"Run cannot transition from {current.value} to {target.value}")
        self.current = current
        self.target = target


class InvalidStepTransitionError(RuntimeError):
    code = RunErrorCode.INVALID_STEP_STATE_TRANSITION

    def __init__(self, current: StepState, target: StepState) -> None:
        super().__init__(f"Run Step cannot transition from {current.value} to {target.value}")
        self.current = current
        self.target = target


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    role: UserRole
    channel: PrincipalChannel

    def __post_init__(self) -> None:
        _validate_identifier(self.user_id, field_name="principal user_id")
        try:
            object.__setattr__(self, "role", UserRole(self.role))
            object.__setattr__(self, "channel", PrincipalChannel(self.channel))
        except ValueError as error:
            raise RunContractError("principal role or channel is invalid") from error


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """Opaque attachment metadata without an external payload or bearer URL."""

    reference: str
    display_name: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    fingerprint: str | None = None

    def __post_init__(self) -> None:
        _validate_bounded_text(self.reference, field_name="attachment reference", limit=512)
        if "://" in self.reference:
            raise RunContractError("attachment reference must be opaque, not an external URL")
        if self.display_name is not None:
            _validate_bounded_text(
                self.display_name,
                field_name="attachment display_name",
                limit=255,
            )
        if self.media_type is not None:
            _validate_bounded_text(self.media_type, field_name="attachment media_type", limit=255)
        if self.size_bytes is not None and self.size_bytes < 0:
            raise RunContractError("attachment size_bytes cannot be negative")
        if self.fingerprint is not None and re.fullmatch(
            r"[0-9a-f]{64}", self.fingerprint
        ) is None:
            raise RunContractError("attachment fingerprint must be a SHA-256 hex digest")


@dataclass(frozen=True, slots=True)
class RunRequest:
    request_id: str
    principal: Principal
    text: str
    idempotency_key: str
    created_at: datetime
    thread_key: str | None = None
    explicit_skill: str | None = None
    schedule_id: str | None = None
    attachments: tuple[AttachmentRef, ...] = ()

    def __post_init__(self) -> None:
        if _REQUEST_ID.fullmatch(self.request_id) is None:
            raise RunContractError("request_id must contain 8-80 URL-safe characters")
        _validate_bounded_text(self.text, field_name="request text", limit=100_000)
        if _IDEMPOTENCY_KEY.fullmatch(self.idempotency_key) is None:
            raise RunContractError("idempotency_key must contain 1-255 visible ASCII characters")
        if self.thread_key is not None:
            _validate_bounded_text(self.thread_key, field_name="thread_key", limit=255)
        if self.explicit_skill is not None:
            _validate_bounded_text(self.explicit_skill, field_name="explicit_skill", limit=255)
        if self.schedule_id is not None:
            _validate_identifier(self.schedule_id, field_name="schedule_id")
        if not isinstance(self.attachments, tuple):
            raise RunContractError("attachments must be an immutable tuple")
        object.__setattr__(self, "created_at", _utc(self.created_at, field_name="created_at"))


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    request: RunRequest
    state: RunState
    updated_at: datetime
    revision: int = 0
    mode: RunMode | None = None
    skill_version_id: str | None = None
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    warnings: tuple[str, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "state", RunState(self.state))
            if self.mode is not None:
                object.__setattr__(self, "mode", RunMode(self.mode))
        except ValueError as error:
            raise RunContractError("run state or mode is invalid") from error
        _validate_identifier(self.id, field_name="run id")
        if self.revision < 0:
            raise RunContractError("run revision cannot be negative")
        if (
            self.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
            and self.finished_at is None
        ):
            raise RunContractError("terminal Run state requires finished_at")
        for field_name in ("skill_version_id", "worker_id"):
            value = getattr(self, field_name)
            if value is not None:
                _validate_identifier(value, field_name=field_name)
        if not isinstance(self.warnings, tuple):
            raise RunContractError("run warnings must be an immutable tuple")
        for warning in self.warnings:
            _validate_bounded_text(warning, field_name="run warning", limit=2_000)
        if self.error_code is not None:
            _validate_bounded_text(self.error_code, field_name="run error_code", limit=120)
        object.__setattr__(self, "updated_at", _utc(self.updated_at, field_name="updated_at"))
        for field_name in (
            "lease_expires_at",
            "heartbeat_at",
            "started_at",
            "finished_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _utc(value, field_name=field_name))


@dataclass(frozen=True, slots=True)
class RunStep:
    id: str
    run_id: str
    node_id: str
    type: str
    state: StepState
    requirement: StepRequirement
    idempotent: bool
    attempt: int
    created_at: datetime
    updated_at: datetime
    depends_on: tuple[str, ...] = ()
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "state", StepState(self.state))
            object.__setattr__(self, "requirement", StepRequirement(self.requirement))
        except ValueError as error:
            raise RunContractError("Run Step state or requirement is invalid") from error
        _validate_identifier(self.id, field_name="step id")
        _validate_identifier(self.run_id, field_name="run id")
        _validate_bounded_text(self.node_id, field_name="node_id", limit=255)
        _validate_bounded_text(self.type, field_name="step type", limit=120)
        if self.attempt < 1:
            raise RunContractError("step attempt must be at least 1")
        if (
            self.state in {StepState.COMPLETED, StepState.FAILED, StepState.CANCELLED}
            and self.finished_at is None
        ):
            raise RunContractError("terminal Run Step state requires finished_at")
        if not isinstance(self.depends_on, tuple):
            raise RunContractError("step depends_on must be an immutable tuple")
        for dependency in self.depends_on:
            _validate_bounded_text(dependency, field_name="step dependency", limit=255)
        if self.error_code is not None:
            _validate_bounded_text(self.error_code, field_name="step error_code", limit=120)
        object.__setattr__(self, "created_at", _utc(self.created_at, field_name="created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, field_name="updated_at"))
        for field_name in ("started_at", "finished_at"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _utc(value, field_name=field_name))


@dataclass(frozen=True, slots=True)
class RunEvent:
    run_id: str
    index: int
    type: str
    visibility: EventVisibility
    created_at: datetime
    step_id: str | None = None
    message: str | None = None
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "visibility", EventVisibility(self.visibility))
        except ValueError as error:
            raise RunContractError("event visibility is invalid") from error
        _validate_identifier(self.run_id, field_name="run id")
        if self.step_id is not None:
            _validate_identifier(self.step_id, field_name="step id")
        if self.index < 1:
            raise RunContractError("event index must be at least 1")
        if _EVENT_TYPE.fullmatch(self.type) is None:
            raise RunContractError("event type must use a lowercase namespace")
        if self.message is not None:
            _validate_bounded_text(self.message, field_name="event message", limit=2_000)
        _validate_attribute_keys(self.attributes)
        object.__setattr__(self, "attributes", _freeze_attributes(self.attributes))
        object.__setattr__(self, "created_at", _utc(self.created_at, field_name="created_at"))


@dataclass(frozen=True, slots=True)
class StepOutcome:
    state: RunState
    warnings: tuple[str, ...]
    error_code: RunErrorCode | None


_RUN_TRANSITIONS: Mapping[RunState, frozenset[RunState]] = MappingProxyType(
    {
        RunState.RECEIVED: frozenset(
            {RunState.BLOCKED, RunState.PLANNING, RunState.QUEUED}
        ),
        RunState.BLOCKED: frozenset({RunState.COMPLETED}),
        RunState.PLANNING: frozenset({RunState.QUEUED, RunState.FAILED}),
        RunState.QUEUED: frozenset({RunState.RUNNING, RunState.CANCELLED}),
        RunState.RUNNING: frozenset(
            {
                RunState.COMPOSING,
                RunState.FAILED,
                RunState.CANCELLED,
                RunState.INTERRUPTED,
            }
        ),
        RunState.INTERRUPTED: frozenset({RunState.QUEUED, RunState.FAILED}),
        RunState.COMPOSING: frozenset({RunState.COMPLETED, RunState.FAILED}),
        RunState.COMPLETED: frozenset(),
        RunState.FAILED: frozenset(),
        RunState.CANCELLED: frozenset(),
    }
)

_STEP_TRANSITIONS: Mapping[StepState, frozenset[StepState]] = MappingProxyType(
    {
        StepState.QUEUED: frozenset({StepState.RUNNING, StepState.CANCELLED}),
        StepState.RUNNING: frozenset(
            {
                StepState.COMPLETED,
                StepState.FAILED,
                StepState.CANCELLED,
                StepState.INTERRUPTED,
            }
        ),
        StepState.INTERRUPTED: frozenset({StepState.QUEUED, StepState.FAILED}),
        StepState.COMPLETED: frozenset(),
        StepState.FAILED: frozenset(),
        StepState.CANCELLED: frozenset(),
    }
)


def allowed_run_transitions(state: RunState) -> frozenset[RunState]:
    return _RUN_TRANSITIONS[state]


def allowed_step_transitions(state: StepState) -> frozenset[StepState]:
    return _STEP_TRANSITIONS[state]


def transition_run(run: Run, target: RunState, *, at: datetime) -> Run:
    if target not in allowed_run_transitions(run.state):
        raise InvalidRunTransitionError(run.state, target)
    timestamp = _utc(at, field_name="transition timestamp")
    started_at = (
        timestamp if target is RunState.RUNNING and run.started_at is None else run.started_at
    )
    finished_at = (
        timestamp
        if target in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
        else run.finished_at
    )
    return replace(
        run,
        state=target,
        updated_at=timestamp,
        revision=run.revision + 1,
        started_at=started_at,
        finished_at=finished_at,
    )


def transition_step(step: RunStep, target: StepState, *, at: datetime) -> RunStep:
    if target not in allowed_step_transitions(step.state):
        raise InvalidStepTransitionError(step.state, target)
    timestamp = _utc(at, field_name="transition timestamp")
    started_at = (
        timestamp if target is StepState.RUNNING and step.started_at is None else step.started_at
    )
    finished_at = (
        timestamp
        if target in {StepState.COMPLETED, StepState.FAILED, StepState.CANCELLED}
        else step.finished_at
    )
    return replace(
        step,
        state=target,
        updated_at=timestamp,
        started_at=started_at,
        finished_at=finished_at,
    )


def resolve_step_outcome(steps: tuple[RunStep, ...]) -> StepOutcome:
    non_terminal = tuple(
        step
        for step in steps
        if step.state not in {StepState.COMPLETED, StepState.FAILED, StepState.CANCELLED}
    )
    if non_terminal:
        raise RunContractError("all Run Steps must be terminal before resolving the outcome")
    failed = tuple(
        step for step in steps if step.state in {StepState.FAILED, StepState.CANCELLED}
    )
    if any(step.requirement is StepRequirement.REQUIRED for step in failed):
        return StepOutcome(RunState.FAILED, (), RunErrorCode.REQUIRED_STEP_FAILED)
    warnings = tuple(
        f"optional step failed: {step.node_id}"
        for step in failed
        if step.requirement is StepRequirement.OPTIONAL
    )
    return StepOutcome(
        RunState.COMPLETED,
        warnings,
        RunErrorCode.OPTIONAL_STEP_FAILED if warnings else None,
    )


def _validate_identifier(value: str, *, field_name: str) -> None:
    if not 16 <= len(value) <= 64 or value.strip() != value:
        raise RunContractError(f"{field_name} must contain 16-64 non-padding characters")


def _validate_bounded_text(value: str, *, field_name: str, limit: int) -> None:
    if not value.strip() or len(value) > limit:
        raise RunContractError(f"{field_name} must contain 1-{limit} non-blank characters")


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RunContractError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_attribute_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise RunContractError("event attribute keys must be strings")
            normalized = key.strip().casefold().replace("-", "_")
            if normalized in RUN_EVENT_FORBIDDEN_ATTRIBUTE_KEYS:
                raise RunContractError(f"event attribute is forbidden: {key}")
            _validate_attribute_keys(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _validate_attribute_keys(nested)
    elif value is None or isinstance(value, (str, bool, int)):
        return
    elif isinstance(value, float):
        if not isfinite(value):
            raise RunContractError("event attribute numbers must be finite")
    else:
        raise RunContractError("event attributes must contain only JSON-compatible values")


def _freeze_attributes(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {key: _freeze_attribute_value(nested) for key, nested in value.items()}
    )


def _freeze_attribute_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_attributes(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_attribute_value(nested) for nested in value)
    return value
