"""Deterministic input-guardrail and protected submission contracts."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from pangi.adapters.outbound.input_rate_limits import InMemoryInputRateLimiter
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.guardrails import (
    ExplicitSkillAccess,
    GuardrailBlockedError,
    InputGuardrailPolicy,
)
from pangi.application.contracts.runs import RunCreation
from pangi.application.services.input_guardrails import (
    GuardedRunSubmissionService,
    InputGuardrailService,
)
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.guardrails import GuardrailErrorCode, GuardrailOutcome, GuardrailStage
from pangi.domain.runs import (
    AttachmentRef,
    Principal,
    PrincipalChannel,
    Run,
    RunRequest,
    RunState,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)
PROHIBITED_CODEPOINTS = frozenset(
    {
        0x00AD,
        0x061C,
        0x200B,
        0x200E,
        0x200F,
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
        0x2060,
        0xFEFF,
    }
)


class StubSkillAuthorizer:
    def __init__(self, access: ExplicitSkillAccess = ExplicitSkillAccess.ALLOWED) -> None:
        self.access = access
        self.calls: list[tuple[str, str]] = []

    async def check_access(
        self,
        *,
        actor: AuthenticatedPrincipal,
        explicit_skill: str,
    ) -> ExplicitSkillAccess:
        self.calls.append((actor.user_id, explicit_skill))
        return self.access


class RecordingRunCreator:
    def __init__(self) -> None:
        self.requests: list[tuple[RunRequest, str]] = []

    async def create_run(self, request: RunRequest, *, route_key: str) -> RunCreation:
        self.requests.append((request, route_key))
        return RunCreation(
            Run(
                id="run-identifier-0001",
                request=request,
                state=RunState.RECEIVED,
                updated_at=request.created_at,
            ),
            False,
        )


def _policy(**changes: object) -> InputGuardrailPolicy:
    values: dict[str, object] = {
        "policy_version": "input-v1",
        "unicode_policy_version": "unicode-v1",
        "max_text_bytes": 128,
        "max_attachment_count": 2,
        "max_attachment_bytes": 10,
        "max_total_attachment_bytes": 15,
        "allowed_media_types": frozenset({"text/plain", "image/png"}),
        "prohibited_codepoints": PROHIBITED_CODEPOINTS,
        "rate_limit": 3,
        "rate_window_seconds": 60,
    }
    values.update(changes)
    return InputGuardrailPolicy(**values)  # type: ignore[arg-type]


def _actor(
    user_id: str = "member-user-00001",
    *,
    role: UserRole = UserRole.MEMBER,
    status: UserStatus = UserStatus.ACTIVE,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(user_id, "Actor", role, status)


def _request(
    *,
    text: str = "안전한 요청",
    user_id: str = "member-user-00001",
    role: UserRole = UserRole.MEMBER,
    explicit_skill: str | None = None,
    attachments: tuple[AttachmentRef, ...] = (),
    idempotency_key: str = "secret-idempotency-key",
) -> RunRequest:
    return RunRequest(
        request_id="request-identifier-1",
        principal=Principal(user_id, role, PrincipalChannel.DASHBOARD),
        text=text,
        idempotency_key=idempotency_key,
        created_at=NOW,
        explicit_skill=explicit_skill,
        attachments=attachments,
    )


def _service(
    *,
    policy: InputGuardrailPolicy | None = None,
    authorizer: StubSkillAuthorizer | None = None,
    limiter: InMemoryInputRateLimiter | None = None,
    now: datetime = NOW,
) -> InputGuardrailService:
    return InputGuardrailService(
        policy or _policy(),
        skill_authorizer=authorizer or StubSkillAuthorizer(),
        rate_limiter=limiter or InMemoryInputRateLimiter(max_keys=100),
        clock=lambda: now,
    )


def _blocked(
    service: InputGuardrailService,
    request: RunRequest,
    *,
    actor: AuthenticatedPrincipal | None = None,
) -> GuardrailBlockedError:
    with pytest.raises(GuardrailBlockedError) as captured:
        asyncio.run(service.guard(actor=actor or _actor(), request=request))
    return captured.value


def test_policy_fingerprint_is_canonical_and_policy_has_no_implicit_limits() -> None:
    first = _policy(allowed_media_types=frozenset({"IMAGE/PNG", "text/plain"}))
    second = _policy(allowed_media_types=frozenset({"text/plain", "image/png"}))

    assert first.allowed_media_types == frozenset({"text/plain", "image/png"})
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    with pytest.raises(TypeError):
        InputGuardrailPolicy()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="max_text_bytes"):
        _policy(max_text_bytes=0)
    with pytest.raises(ValueError, match="media type"):
        _policy(allowed_media_types=frozenset({"not-a-media-type"}))


@pytest.mark.parametrize(
    ("actor", "run_request", "code"),
    (
        (
            _actor(status=UserStatus.DISABLED),
            _request(),
            GuardrailErrorCode.PRINCIPAL_INACTIVE,
        ),
        (
            _actor("another-user-0001"),
            _request(),
            GuardrailErrorCode.PRINCIPAL_ID_MISMATCH,
        ),
        (
            _actor(role=UserRole.ADMIN),
            _request(),
            GuardrailErrorCode.PRINCIPAL_ROLE_MISMATCH,
        ),
    ),
)
def test_principal_must_be_active_and_match_request(
    actor: AuthenticatedPrincipal,
    run_request: RunRequest,
    code: GuardrailErrorCode,
) -> None:
    error = _blocked(_service(), run_request, actor=actor)

    assert error.code is code
    assert error.decision.stage is GuardrailStage.PRINCIPAL
    assert error.decision.outcome is GuardrailOutcome.BLOCKED


def test_normalizes_line_endings_nfc_media_type_and_preserves_emoji_joiner() -> None:
    decomposed = "Cafe\u0301\r\n다음\r줄\t👩‍💻"
    attachment = AttachmentRef(
        "opaque-attachment-1",
        media_type=" Image/PNG ",
        size_bytes=4,
    )

    guarded = asyncio.run(
        _service().guard(
            actor=_actor(),
            request=_request(text=decomposed, attachments=(attachment,)),
        )
    )

    assert guarded.request.text == "Café\n다음\n줄\t👩‍💻"
    assert guarded.request.attachments[0].media_type == "image/png"
    assert guarded.decision.text_bytes == len(guarded.request.text.encode())
    assert guarded.decision.stage is GuardrailStage.COMPLETE
    assert guarded.decision.outcome is GuardrailOutcome.ALLOWED
    assert len(guarded.decision.policy_fingerprint) == 64


def test_text_limit_uses_utf8_bytes_for_korean_and_emoji() -> None:
    request = _request(text="한🙂")
    accepted = asyncio.run(
        _service(policy=_policy(max_text_bytes=7)).guard(actor=_actor(), request=request)
    )
    rejected = _blocked(_service(policy=_policy(max_text_bytes=6)), request)

    assert accepted.decision.text_bytes == 7
    assert rejected.code is GuardrailErrorCode.TEXT_BYTES_EXCEEDED
    assert rejected.decision.text_bytes == 7


@pytest.mark.parametrize("unsafe_text", ("safe\0text", "safe\u202etext", "safe\u200btext"))
def test_rejects_control_bidi_and_policy_hidden_unicode(unsafe_text: str) -> None:
    error = _blocked(_service(), _request(text=unsafe_text))

    assert error.code is GuardrailErrorCode.UNSAFE_UNICODE
    assert error.decision.stage is GuardrailStage.NORMALIZATION


@pytest.mark.parametrize(
    ("attachments", "code"),
    (
        (
            tuple(
                AttachmentRef(f"opaque-attachment-{index}", media_type="text/plain", size_bytes=1)
                for index in range(3)
            ),
            GuardrailErrorCode.ATTACHMENT_COUNT_EXCEEDED,
        ),
        (
            (AttachmentRef("opaque-attachment-1", size_bytes=1),),
            GuardrailErrorCode.ATTACHMENT_METADATA_MISSING,
        ),
        (
            (AttachmentRef("opaque-attachment-1", media_type="text/plain"),),
            GuardrailErrorCode.ATTACHMENT_METADATA_MISSING,
        ),
        (
            (AttachmentRef("opaque-attachment-1", media_type="text/plain", size_bytes=11),),
            GuardrailErrorCode.ATTACHMENT_BYTES_EXCEEDED,
        ),
        (
            (
                AttachmentRef("opaque-attachment-1", media_type="text/plain", size_bytes=8),
                AttachmentRef("opaque-attachment-2", media_type="text/plain", size_bytes=8),
            ),
            GuardrailErrorCode.ATTACHMENT_TOTAL_BYTES_EXCEEDED,
        ),
        (
            (AttachmentRef("opaque-attachment-1", media_type="application/pdf", size_bytes=1),),
            GuardrailErrorCode.ATTACHMENT_MEDIA_TYPE_DENIED,
        ),
    ),
)
def test_attachment_limits_and_required_metadata_are_deterministic(
    attachments: tuple[AttachmentRef, ...],
    code: GuardrailErrorCode,
) -> None:
    error = _blocked(_service(), _request(attachments=attachments))

    assert error.code is code
    assert error.decision.stage is GuardrailStage.ATTACHMENTS
    assert error.decision.attachment_count == len(attachments)


@pytest.mark.parametrize(
    ("access", "code"),
    (
        (ExplicitSkillAccess.DENIED, GuardrailErrorCode.EXPLICIT_SKILL_DENIED),
        (ExplicitSkillAccess.UNAVAILABLE, GuardrailErrorCode.EXPLICIT_SKILL_UNAVAILABLE),
    ),
)
def test_explicit_skill_must_be_allowed(
    access: ExplicitSkillAccess,
    code: GuardrailErrorCode,
) -> None:
    authorizer = StubSkillAuthorizer(access)
    request = _request(explicit_skill="weekly-digest@candidate")

    error = _blocked(_service(authorizer=authorizer), request)

    assert error.code is code
    assert authorizer.calls == [("member-user-00001", "weekly-digest@candidate")]


def test_rate_limit_is_actor_channel_scoped_and_reports_retry_delay() -> None:
    limiter = InMemoryInputRateLimiter(max_keys=2)
    service = _service(policy=_policy(rate_limit=2, rate_window_seconds=60), limiter=limiter)
    request = _request()

    asyncio.run(service.guard(actor=_actor(), request=request))
    asyncio.run(service.guard(actor=_actor(), request=request))
    error = _blocked(service, request)

    assert error.code is GuardrailErrorCode.RATE_LIMIT_EXCEEDED
    assert error.decision.retry_after_seconds == 60
    other_channel = replace(
        request,
        principal=replace(request.principal, channel=PrincipalChannel.API),
    )
    asyncio.run(service.guard(actor=_actor(), request=other_channel))


def test_in_memory_rate_limiter_resets_window_and_bounds_keys() -> None:
    limiter = InMemoryInputRateLimiter(max_keys=1)

    assert limiter.reserve("first", at=NOW, limit=1, window_seconds=10) is None
    assert limiter.reserve("first", at=NOW, limit=1, window_seconds=10) == 10
    assert (
        limiter.reserve("second", at=NOW + timedelta(seconds=1), limit=1, window_seconds=10) is None
    )
    assert (
        limiter.reserve("first", at=NOW + timedelta(seconds=2), limit=1, window_seconds=10) is None
    )
    assert (
        limiter.reserve("first", at=NOW + timedelta(seconds=12), limit=1, window_seconds=10) is None
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        limiter.reserve("first", at=datetime(2030, 1, 1), limit=1, window_seconds=10)


def test_blocked_submission_never_calls_run_creator_and_errors_are_secret_safe() -> None:
    raw_text = "raw-super-secret\0payload"
    idempotency_key = "secret-idempotency-key"
    creator = RecordingRunCreator()
    submission = GuardedRunSubmissionService(_service(), run_creator=creator)

    with pytest.raises(GuardrailBlockedError) as captured:
        asyncio.run(
            submission.submit(
                actor=_actor(),
                request=_request(text=raw_text, idempotency_key=idempotency_key),
                route_key="runs.create",
            )
        )

    assert creator.requests == []
    rendered = f"{captured.value!s} {captured.value!r} {captured.value.decision!r}"
    assert raw_text not in rendered
    assert idempotency_key not in rendered


def test_allowed_submission_uses_normalized_request_and_safe_result_repr() -> None:
    raw_text = "Cafe\u0301\r\nraw-super-secret"
    attachment_reference = "opaque-secret-attachment-reference"
    creator = RecordingRunCreator()
    submission = GuardedRunSubmissionService(_service(), run_creator=creator)
    request = _request(
        text=raw_text,
        attachments=(
            AttachmentRef(
                attachment_reference,
                media_type="text/plain",
                size_bytes=1,
            ),
        ),
    )

    result = asyncio.run(
        submission.submit(actor=_actor(), request=request, route_key="runs.create")
    )

    assert creator.requests[0][0].text == "Café\nraw-super-secret"
    assert creator.requests[0][1] == "runs.create"
    rendered = repr(result)
    assert raw_text not in rendered
    assert request.idempotency_key not in rendered
    assert attachment_reference not in rendered
    assert result.decision.as_dict()["policy_version"] == "input-v1"
