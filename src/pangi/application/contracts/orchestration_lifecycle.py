"""Secret-safe contracts for orchestration Run planning and completion."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pangi.domain.runs import RunMode, RunState

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,119}$")


@dataclass(frozen=True, slots=True)
class OrchestrationPlanningToken:
    run_id: str
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not 16 <= len(self.run_id) <= 64:
            raise ValueError("run_id must contain 16-64 characters")
        if self.run_id.strip() != self.run_id:
            raise ValueError("run_id cannot contain surrounding whitespace")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise ValueError("revision must be an integer")
        if self.revision < 1:
            raise ValueError("planning revision must be positive")


@dataclass(frozen=True, slots=True)
class OrchestrationDecisionRecord:
    mode: RunMode
    logical_call_count: int
    provider_request_count: int
    plan_fingerprint: str | None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "mode", RunMode(self.mode))
        except ValueError as error:
            raise ValueError("decision mode is invalid") from error
        if self.logical_call_count not in {0, 1}:
            raise ValueError("logical_call_count must be 0 or 1")
        if (
            isinstance(self.provider_request_count, bool)
            or not isinstance(self.provider_request_count, int)
            or not 0 <= self.provider_request_count <= 10
        ):
            raise ValueError("provider_request_count must be between 0 and 10")
        if self.logical_call_count == 0 and self.provider_request_count != 0:
            raise ValueError("a zero-call decision cannot contain Provider requests")
        if self.logical_call_count == 1 and self.provider_request_count < 1:
            raise ValueError("a Model decision requires at least one Provider request")
        if self.mode is RunMode.SKILL:
            if self.plan_fingerprint is not None:
                raise ValueError("a Skill decision cannot contain an execution fingerprint")
        elif self.plan_fingerprint is None or _FINGERPRINT.fullmatch(self.plan_fingerprint) is None:
            raise ValueError("an executable decision requires a Plan fingerprint")


@dataclass(frozen=True, slots=True)
class OrchestrationFailureRecord:
    error_code: str
    logical_call_count: int | None = None
    provider_request_count: int | None = None

    def __post_init__(self) -> None:
        if _ERROR_CODE.fullmatch(self.error_code) is None:
            raise ValueError("error_code must be a stable lowercase identifier")
        for value, field_name, maximum in (
            (self.logical_call_count, "logical_call_count", 1),
            (self.provider_request_count, "provider_request_count", 10),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum
            ):
                raise ValueError(f"{field_name} is outside its supported range")
        if self.logical_call_count == 0 and self.provider_request_count not in {None, 0}:
            raise ValueError("a zero-call failure cannot contain Provider requests")


@dataclass(frozen=True, slots=True)
class OrchestrationSubmissionResult:
    run_id: str
    state: RunState
    replayed: bool
    plan_fingerprint: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not 16 <= len(self.run_id) <= 64:
            raise ValueError("run_id must contain 16-64 characters")
        try:
            object.__setattr__(self, "state", RunState(self.state))
        except ValueError as error:
            raise ValueError("submission state is invalid") from error
        if not isinstance(self.replayed, bool):
            raise ValueError("replayed must be a boolean")
        if (
            self.plan_fingerprint is not None
            and _FINGERPRINT.fullmatch(self.plan_fingerprint) is None
        ):
            raise ValueError("plan_fingerprint must be a SHA-256 hex digest")
        if self.error_code is not None and _ERROR_CODE.fullmatch(self.error_code) is None:
            raise ValueError("error_code must be a stable lowercase identifier")
