"""Explicit HTTP schemas for the Pangi Admin API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pangi.application.contracts.auth import AuthenticatedPrincipal, SessionView
from pangi.application.contracts.bootstrap import BootstrapAdminResult
from pangi.application.contracts.run_events import RunEventPage, RunQueueMetrics
from pangi.application.contracts.run_queue import RunCancellation
from pangi.application.contracts.runs import RunListPage, RunSummary
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.runs import (
    AttachmentRef,
    EventVisibility,
    PrincipalChannel,
    Run,
    RunEvent,
    RunMode,
    RunState,
)


class _StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BootstrapAdminRequest(_StrictApiModel):
    """One-time credentials used to create the first local administrator."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )

    token: str = Field(min_length=20, max_length=256, json_schema_extra={"writeOnly": True})
    local_id: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(
        min_length=12,
        max_length=256,
        json_schema_extra={"writeOnly": True},
    )


class LoginRequest(_StrictApiModel):
    """Local administrator credentials."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )

    local_id: str = Field(min_length=1, max_length=80)
    password: str = Field(
        min_length=1,
        max_length=256,
        json_schema_extra={"writeOnly": True},
    )


class AdminResponse(_StrictApiModel):
    user_id: str
    local_id: str
    display_name: str
    role: Literal["admin"]
    status: Literal["active"]

    @classmethod
    def from_contract(cls, result: BootstrapAdminResult) -> AdminResponse:
        return cls(
            user_id=result.user_id,
            local_id=result.local_id,
            display_name=result.display_name,
            role="admin",
            status="active",
        )


class BootstrapAdminResponse(_StrictApiModel):
    admin: AdminResponse

    @classmethod
    def from_contract(cls, result: BootstrapAdminResult) -> BootstrapAdminResponse:
        return cls(admin=AdminResponse.from_contract(result))


class PrincipalResponse(_StrictApiModel):
    user_id: str
    display_name: str
    role: UserRole
    status: UserStatus

    @classmethod
    def from_contract(cls, principal: AuthenticatedPrincipal) -> PrincipalResponse:
        return cls(
            user_id=principal.user_id,
            display_name=principal.display_name,
            role=principal.role,
            status=principal.status,
        )


class SessionResponse(_StrictApiModel):
    principal: PrincipalResponse
    expires_at: datetime
    rotation_due_at: datetime
    rotation_due: bool

    @classmethod
    def from_contract(cls, session: SessionView) -> SessionResponse:
        return cls(
            principal=PrincipalResponse.from_contract(session.principal),
            expires_at=session.expires_at,
            rotation_due_at=session.rotation_due_at,
            rotation_due=session.rotation_due,
        )


class SessionEnvelope(_StrictApiModel):
    session: SessionResponse

    @classmethod
    def from_contract(cls, session: SessionView) -> SessionEnvelope:
        return cls(session=SessionResponse.from_contract(session))


class AttachmentResponse(_StrictApiModel):
    reference: str
    display_name: str | None
    media_type: str | None
    size_bytes: int | None
    fingerprint: str | None

    @classmethod
    def from_domain(cls, attachment: AttachmentRef) -> AttachmentResponse:
        return cls(
            reference=attachment.reference,
            display_name=attachment.display_name,
            media_type=attachment.media_type,
            size_bytes=attachment.size_bytes,
            fingerprint=attachment.fingerprint,
        )


class RunRequestResponse(_StrictApiModel):
    request_id: str
    principal_id: str
    trigger: PrincipalChannel
    text: str
    thread_key: str | None
    explicit_skill: str | None
    schedule_id: str | None
    attachments: tuple[AttachmentResponse, ...]
    created_at: datetime


class RunResponse(_StrictApiModel):
    id: str
    request: RunRequestResponse
    state: RunState
    mode: RunMode | None
    skill_version_id: str | None
    revision: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    warnings: tuple[str, ...]
    error_code: str | None

    @classmethod
    def from_domain(cls, run: Run) -> RunResponse:
        request = run.request
        return cls(
            id=run.id,
            request=RunRequestResponse(
                request_id=request.request_id,
                principal_id=request.principal.user_id,
                trigger=request.principal.channel,
                text=request.text,
                thread_key=request.thread_key,
                explicit_skill=request.explicit_skill,
                schedule_id=request.schedule_id,
                attachments=tuple(
                    AttachmentResponse.from_domain(attachment)
                    for attachment in request.attachments
                ),
                created_at=request.created_at,
            ),
            state=run.state,
            mode=run.mode,
            skill_version_id=run.skill_version_id,
            revision=run.revision,
            created_at=request.created_at,
            updated_at=run.updated_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            warnings=run.warnings,
            error_code=run.error_code,
        )


class RunEnvelope(_StrictApiModel):
    run: RunResponse

    @classmethod
    def from_domain(cls, run: Run) -> RunEnvelope:
        return cls(run=RunResponse.from_domain(run))


class RunSummaryResponse(_StrictApiModel):
    id: str
    request_id: str
    principal_id: str
    trigger: PrincipalChannel
    state: RunState
    mode: RunMode | None
    skill_version_id: str | None
    revision: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    warning_count: int
    error_code: str | None

    @classmethod
    def from_contract(cls, summary: RunSummary) -> RunSummaryResponse:
        return cls(**{
            field: getattr(summary, field)
            for field in cls.model_fields
        })


class RunListEnvelope(_StrictApiModel):
    items: tuple[RunSummaryResponse, ...]
    next_cursor: str | None

    @classmethod
    def from_contract(cls, page: RunListPage) -> RunListEnvelope:
        return cls(
            items=tuple(RunSummaryResponse.from_contract(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


class RunCancellationEnvelope(_StrictApiModel):
    run: RunResponse
    changed: bool

    @classmethod
    def from_contract(cls, result: RunCancellation) -> RunCancellationEnvelope:
        return cls(run=RunResponse.from_domain(result.run), changed=result.changed)


class RunEventResponse(_StrictApiModel):
    run_id: str
    index: int
    type: str
    visibility: EventVisibility
    step_id: str | None
    message: str | None
    attributes: dict[str, object]
    created_at: datetime

    @classmethod
    def from_domain(cls, event: RunEvent) -> RunEventResponse:
        return cls(
            run_id=event.run_id,
            index=event.index,
            type=event.type,
            visibility=event.visibility,
            step_id=event.step_id,
            message=event.message,
            attributes=dict(event.attributes),
            created_at=event.created_at,
        )


class RunEventListEnvelope(_StrictApiModel):
    items: tuple[RunEventResponse, ...]
    next_after_index: int | None
    terminal: bool

    @classmethod
    def from_contract(cls, page: RunEventPage) -> RunEventListEnvelope:
        return cls(
            items=tuple(RunEventResponse.from_domain(item) for item in page.items),
            next_after_index=page.next_after_index,
            terminal=page.terminal,
        )


class RunQueueMetricsResponse(_StrictApiModel):
    queue_depth: int
    running_count: int
    expired_lease_count: int
    oldest_queued_at: datetime | None
    oldest_queued_age_seconds: float | None

    @classmethod
    def from_contract(cls, metrics: RunQueueMetrics) -> RunQueueMetricsResponse:
        return cls(**{
            field: getattr(metrics, field)
            for field in cls.model_fields
        })


class ErrorBody(_StrictApiModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(_StrictApiModel):
    error: ErrorBody

    @classmethod
    def create(cls, *, code: str, message: str, request_id: str) -> ErrorEnvelope:
        return cls(error=ErrorBody(code=code, message=message, request_id=request_id))
