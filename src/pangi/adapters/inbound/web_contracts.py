"""Explicit HTTP schemas for the Pangi Admin API."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pangi.application.contracts.audit import AuditListPage
from pangi.application.contracts.auth import AuthenticatedPrincipal, SessionView
from pangi.application.contracts.bootstrap import BootstrapAdminResult
from pangi.application.contracts.model_policy_management import (
    ModelInvocationSummary,
    ModelPolicyActivation,
    ModelPolicyEvaluation,
    ModelPolicyImpact,
    ModelPolicyListPage,
    ModelPolicyVersion,
)
from pangi.application.contracts.run_events import RunEventPage, RunQueueMetrics
from pangi.application.contracts.run_queue import RunCancellation
from pangi.application.contracts.runs import RunListPage, RunSummary
from pangi.domain.audit import AuditEvent, AuditOutcome
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.model_routing import (
    DataClass,
    ModelPolicyState,
    ModelPurpose,
    ModelRetention,
)
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


def _json_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _json_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


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


class AuditEventResponse(_StrictApiModel):
    id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    outcome: AuditOutcome
    metadata: dict[str, object]
    created_at: datetime

    @classmethod
    def from_domain(cls, event: AuditEvent) -> AuditEventResponse:
        return cls(
            id=event.id,
            actor_id=event.actor_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome=event.outcome,
            metadata=_json_mapping(event.metadata),
            created_at=event.created_at,
        )


class AuditEventListEnvelope(_StrictApiModel):
    items: tuple[AuditEventResponse, ...]
    next_cursor: str | None

    @classmethod
    def from_contract(cls, page: AuditListPage) -> AuditEventListEnvelope:
        return cls(
            items=tuple(AuditEventResponse.from_domain(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


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
                    AttachmentResponse.from_domain(attachment) for attachment in request.attachments
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
        return cls(**{field: getattr(summary, field) for field in cls.model_fields})


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
        return cls(**{field: getattr(metrics, field) for field in cls.model_fields})


class ModelEgressPolicyResponse(_StrictApiModel):
    policy_id: str
    policy_version: str
    profile: str
    allowed_providers: tuple[str, ...]
    allowed_models: tuple[str, ...]
    allowed_regions: tuple[str, ...]
    allowed_data_classes: tuple[DataClass, ...]
    allowed_source_kinds: tuple[str, ...]
    allowed_purposes: tuple[ModelPurpose, ...]
    require_redaction: bool
    require_zero_retention: bool
    allow_raw_content: bool


class ModelProfileResponse(_StrictApiModel):
    profile_id: str
    profile_version: str
    profile: str
    provider: str
    model: str
    region: str | None
    supported_data_classes: tuple[DataClass, ...]
    supported_source_kinds: tuple[str, ...]
    supported_purposes: tuple[ModelPurpose, ...]
    retention: ModelRetention
    allow_raw_content: bool
    routing_priority: int
    active: bool
    fingerprint: str


class ModelInvocationPurposeCountResponse(_StrictApiModel):
    purpose: str
    count: int


class ModelInvocationReasonCountResponse(_StrictApiModel):
    reason: str
    count: int


class ModelInvocationSummaryResponse(_StrictApiModel):
    window_started_at: datetime
    window_ended_at: datetime
    allowed_count: int
    denied_count: int
    purposes: tuple[ModelInvocationPurposeCountResponse, ...]
    denial_reasons: tuple[ModelInvocationReasonCountResponse, ...]

    @classmethod
    def from_contract(
        cls,
        summary: ModelInvocationSummary,
    ) -> ModelInvocationSummaryResponse:
        return cls(
            window_started_at=summary.window_started_at,
            window_ended_at=summary.window_ended_at,
            allowed_count=summary.allowed_count,
            denied_count=summary.denied_count,
            purposes=tuple(
                ModelInvocationPurposeCountResponse(
                    purpose=item.purpose,
                    count=item.count,
                )
                for item in summary.purposes
            ),
            denial_reasons=tuple(
                ModelInvocationReasonCountResponse(
                    reason=item.reason,
                    count=item.count,
                )
                for item in summary.denial_reasons
            ),
        )


class ModelPolicyImpactResponse(_StrictApiModel):
    schema_version: Literal["model-policy-impact-v1"]
    impact_fingerprint: str
    candidate_snapshot_fingerprint: str
    baseline_snapshot_fingerprint: str | None
    added_policy_keys: tuple[str, ...]
    removed_policy_keys: tuple[str, ...]
    changed_policy_keys: tuple[str, ...]
    affected_policy_keys: tuple[str, ...]
    consumer_resolution: Literal["unavailable"]
    affected_consumers: tuple[str, ...]
    required_eval_suites: tuple[str, ...]

    @classmethod
    def from_contract(cls, impact: ModelPolicyImpact) -> ModelPolicyImpactResponse:
        return cls(
            schema_version="model-policy-impact-v1",
            impact_fingerprint=impact.fingerprint,
            candidate_snapshot_fingerprint=impact.candidate_snapshot_fingerprint,
            baseline_snapshot_fingerprint=impact.baseline_snapshot_fingerprint,
            added_policy_keys=impact.added_policy_keys,
            removed_policy_keys=impact.removed_policy_keys,
            changed_policy_keys=impact.changed_policy_keys,
            affected_policy_keys=impact.affected_policy_keys,
            consumer_resolution=impact.consumer_resolution,
            affected_consumers=(),
            required_eval_suites=(),
        )


class ModelPolicyVersionResponse(_StrictApiModel):
    policy_id: str
    version: str
    profile: str
    fingerprint: str
    state: ModelPolicyState
    eval_run_id: str | None
    egress_policy: ModelEgressPolicyResponse
    profiles: tuple[ModelProfileResponse, ...]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_contract(cls, version: ModelPolicyVersion) -> ModelPolicyVersionResponse:
        policy = version.snapshot.policy
        return cls(
            policy_id=version.policy_id,
            version=version.version,
            profile=version.profile,
            fingerprint=version.fingerprint,
            state=version.state,
            eval_run_id=version.eval_run_id,
            egress_policy=ModelEgressPolicyResponse(
                policy_id=policy.policy_id,
                policy_version=policy.policy_version,
                profile=policy.profile,
                allowed_providers=tuple(sorted(policy.allowed_providers)),
                allowed_models=tuple(sorted(policy.allowed_models)),
                allowed_regions=tuple(sorted(policy.allowed_regions)),
                allowed_data_classes=tuple(
                    sorted(policy.allowed_data_classes, key=lambda item: item.value)
                ),
                allowed_source_kinds=tuple(sorted(policy.allowed_source_kinds)),
                allowed_purposes=tuple(
                    sorted(policy.allowed_purposes, key=lambda item: item.value)
                ),
                require_redaction=policy.require_redaction,
                require_zero_retention=policy.require_zero_retention,
                allow_raw_content=policy.allow_raw_content,
            ),
            profiles=tuple(
                ModelProfileResponse(
                    profile_id=profile.profile_id,
                    profile_version=profile.profile_version,
                    profile=profile.profile,
                    provider=profile.provider,
                    model=profile.model,
                    region=profile.region,
                    supported_data_classes=tuple(
                        sorted(profile.supported_data_classes, key=lambda item: item.value)
                    ),
                    supported_source_kinds=tuple(sorted(profile.supported_source_kinds)),
                    supported_purposes=tuple(
                        sorted(profile.supported_purposes, key=lambda item: item.value)
                    ),
                    retention=profile.retention,
                    allow_raw_content=profile.allow_raw_content,
                    routing_priority=profile.routing_priority,
                    active=profile.active,
                    fingerprint=profile.fingerprint,
                )
                for profile in version.snapshot.profiles
            ),
            created_at=version.created_at,
            updated_at=version.updated_at,
        )


class ModelPolicyListItemResponse(_StrictApiModel):
    policy: ModelPolicyVersionResponse
    invocation_summary: ModelInvocationSummaryResponse
    impact: ModelPolicyImpactResponse | None


class ModelPolicyListEnvelope(_StrictApiModel):
    items: tuple[ModelPolicyListItemResponse, ...]
    next_cursor: str | None

    @classmethod
    def from_contract(cls, page: ModelPolicyListPage) -> ModelPolicyListEnvelope:
        return cls(
            items=tuple(
                ModelPolicyListItemResponse(
                    policy=ModelPolicyVersionResponse.from_contract(item.policy),
                    invocation_summary=ModelInvocationSummaryResponse.from_contract(
                        item.invocation_summary
                    ),
                    impact=(
                        None
                        if item.impact is None
                        else ModelPolicyImpactResponse.from_contract(item.impact)
                    ),
                )
                for item in page.items
            ),
            next_cursor=page.next_cursor,
        )


class ModelPolicyEvaluateRequest(_StrictApiModel):
    candidate_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class ModelPolicyEvaluationEnvelope(_StrictApiModel):
    eval_run_id: str
    state: Literal["queued", "running", "passed", "failed"]
    impact: ModelPolicyImpactResponse

    @classmethod
    def from_contract(
        cls,
        evaluation: ModelPolicyEvaluation,
    ) -> ModelPolicyEvaluationEnvelope:
        return cls(
            eval_run_id=evaluation.eval_run_id,
            state=evaluation.state,
            impact=ModelPolicyImpactResponse.from_contract(evaluation.impact),
        )


class ModelPolicyActivateRequest(_StrictApiModel):
    candidate_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    impact_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    eval_run_id: str = Field(min_length=16, max_length=64)


class ModelPolicyActivationEnvelope(_StrictApiModel):
    policy: ModelPolicyVersionResponse
    impact_fingerprint: str
    replayed: bool

    @classmethod
    def from_contract(
        cls,
        activation: ModelPolicyActivation,
    ) -> ModelPolicyActivationEnvelope:
        return cls(
            policy=ModelPolicyVersionResponse.from_contract(activation.policy),
            impact_fingerprint=activation.impact_fingerprint,
            replayed=activation.replayed,
        )


class ErrorBody(_StrictApiModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(_StrictApiModel):
    error: ErrorBody

    @classmethod
    def create(cls, *, code: str, message: str, request_id: str) -> ErrorEnvelope:
        return cls(error=ErrorBody(code=code, message=message, request_id=request_id))
