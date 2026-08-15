"""Deterministic input normalization and guarded Run submission."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.guardrails import (
    ExplicitSkillAccess,
    GuardedRunCreation,
    GuardedRunRequest,
    GuardrailBlockedError,
    GuardrailDecision,
    InputGuardrailPolicy,
)
from pangi.application.ports.guardrails import (
    ExplicitSkillAuthorizer,
    InputRateLimiter,
    RunCreator,
)
from pangi.domain.auth import UserStatus
from pangi.domain.guardrails import (
    GuardrailErrorCode,
    GuardrailOutcome,
    GuardrailStage,
    TrustLevel,
)
from pangi.domain.runs import AttachmentRef, RunRequest

Clock = Callable[[], datetime]


class InputGuardrailService:
    """Evaluate untrusted Run input in a fixed, auditable order."""

    def __init__(
        self,
        policy: InputGuardrailPolicy,
        *,
        skill_authorizer: ExplicitSkillAuthorizer,
        rate_limiter: InputRateLimiter,
        clock: Clock,
    ) -> None:
        self._policy = policy
        self._skill_authorizer = skill_authorizer
        self._rate_limiter = rate_limiter
        self._clock = clock

    async def guard(
        self,
        *,
        actor: AuthenticatedPrincipal,
        request: RunRequest,
    ) -> GuardedRunRequest:
        attachment_count = len(request.attachments)
        if actor.status is not UserStatus.ACTIVE:
            self._block(
                GuardrailStage.PRINCIPAL,
                GuardrailErrorCode.PRINCIPAL_INACTIVE,
                attachment_count=attachment_count,
            )
        if actor.user_id != request.principal.user_id:
            self._block(
                GuardrailStage.PRINCIPAL,
                GuardrailErrorCode.PRINCIPAL_ID_MISMATCH,
                attachment_count=attachment_count,
            )
        if actor.role is not request.principal.role:
            self._block(
                GuardrailStage.PRINCIPAL,
                GuardrailErrorCode.PRINCIPAL_ROLE_MISMATCH,
                attachment_count=attachment_count,
            )

        normalized_text = unicodedata.normalize(
            "NFC",
            request.text.replace("\r\n", "\n").replace("\r", "\n"),
        )
        if any(self._is_prohibited(character) for character in normalized_text):
            self._block(
                GuardrailStage.NORMALIZATION,
                GuardrailErrorCode.UNSAFE_UNICODE,
                attachment_count=attachment_count,
            )
        text_bytes = len(normalized_text.encode("utf-8"))
        if text_bytes > self._policy.max_text_bytes:
            self._block(
                GuardrailStage.TEXT,
                GuardrailErrorCode.TEXT_BYTES_EXCEEDED,
                text_bytes=text_bytes,
                attachment_count=attachment_count,
            )

        normalized_attachments = self._guard_attachments(
            request.attachments,
            text_bytes=text_bytes,
        )
        if request.explicit_skill is not None:
            access_value: object = await self._skill_authorizer.check_access(
                actor=actor,
                explicit_skill=request.explicit_skill,
            )
            if not isinstance(access_value, ExplicitSkillAccess):
                self._block(
                    GuardrailStage.EXPLICIT_SKILL,
                    GuardrailErrorCode.EXPLICIT_SKILL_UNAVAILABLE,
                    text_bytes=text_bytes,
                    attachment_count=attachment_count,
                )
            if access_value is ExplicitSkillAccess.DENIED:
                self._block(
                    GuardrailStage.EXPLICIT_SKILL,
                    GuardrailErrorCode.EXPLICIT_SKILL_DENIED,
                    text_bytes=text_bytes,
                    attachment_count=attachment_count,
                )
            if access_value is ExplicitSkillAccess.UNAVAILABLE:
                self._block(
                    GuardrailStage.EXPLICIT_SKILL,
                    GuardrailErrorCode.EXPLICIT_SKILL_UNAVAILABLE,
                    text_bytes=text_bytes,
                    attachment_count=attachment_count,
                )

        retry_after = self._rate_limiter.reserve(
            self._rate_key(actor, request),
            at=self._clock(),
            limit=self._policy.rate_limit,
            window_seconds=self._policy.rate_window_seconds,
        )
        if retry_after is not None:
            self._block(
                GuardrailStage.RATE_LIMIT,
                GuardrailErrorCode.RATE_LIMIT_EXCEEDED,
                text_bytes=text_bytes,
                attachment_count=attachment_count,
                retry_after_seconds=retry_after,
            )

        normalized_request = replace(
            request,
            text=normalized_text,
            attachments=normalized_attachments,
        )
        return GuardedRunRequest(
            request=normalized_request,
            decision=self._decision(
                stage=GuardrailStage.COMPLETE,
                outcome=GuardrailOutcome.ALLOWED,
                text_bytes=text_bytes,
                attachment_count=attachment_count,
            ),
        )

    def _guard_attachments(
        self,
        attachments: tuple[AttachmentRef, ...],
        *,
        text_bytes: int,
    ) -> tuple[AttachmentRef, ...]:
        attachment_count = len(attachments)
        if attachment_count > self._policy.max_attachment_count:
            self._block(
                GuardrailStage.ATTACHMENTS,
                GuardrailErrorCode.ATTACHMENT_COUNT_EXCEEDED,
                text_bytes=text_bytes,
                attachment_count=attachment_count,
            )
        total_bytes = 0
        normalized: list[AttachmentRef] = []
        for attachment in attachments:
            if attachment.media_type is None or attachment.size_bytes is None:
                self._block(
                    GuardrailStage.ATTACHMENTS,
                    GuardrailErrorCode.ATTACHMENT_METADATA_MISSING,
                    text_bytes=text_bytes,
                    attachment_count=attachment_count,
                )
            assert attachment.media_type is not None
            assert attachment.size_bytes is not None
            if attachment.size_bytes > self._policy.max_attachment_bytes:
                self._block(
                    GuardrailStage.ATTACHMENTS,
                    GuardrailErrorCode.ATTACHMENT_BYTES_EXCEEDED,
                    text_bytes=text_bytes,
                    attachment_count=attachment_count,
                )
            total_bytes += attachment.size_bytes
            if total_bytes > self._policy.max_total_attachment_bytes:
                self._block(
                    GuardrailStage.ATTACHMENTS,
                    GuardrailErrorCode.ATTACHMENT_TOTAL_BYTES_EXCEEDED,
                    text_bytes=text_bytes,
                    attachment_count=attachment_count,
                )
            media_type = attachment.media_type.strip().casefold()
            if media_type not in self._policy.allowed_media_types:
                self._block(
                    GuardrailStage.ATTACHMENTS,
                    GuardrailErrorCode.ATTACHMENT_MEDIA_TYPE_DENIED,
                    text_bytes=text_bytes,
                    attachment_count=attachment_count,
                )
            normalized.append(replace(attachment, media_type=media_type))
        return tuple(normalized)

    def _is_prohibited(self, character: str) -> bool:
        if ord(character) in self._policy.prohibited_codepoints:
            return True
        return unicodedata.category(character) == "Cc" and character not in {"\t", "\n"}

    @staticmethod
    def _rate_key(actor: AuthenticatedPrincipal, request: RunRequest) -> str:
        scope = f"{actor.user_id}\0{request.principal.channel.value}".encode()
        return hashlib.sha256(scope).hexdigest()

    def _decision(
        self,
        *,
        stage: GuardrailStage,
        outcome: GuardrailOutcome,
        text_bytes: int | None,
        attachment_count: int,
        error_code: GuardrailErrorCode | None = None,
        retry_after_seconds: int | None = None,
    ) -> GuardrailDecision:
        return GuardrailDecision(
            trust_level=TrustLevel.UNTRUSTED,
            stage=stage,
            outcome=outcome,
            policy_version=self._policy.policy_version,
            policy_fingerprint=self._policy.fingerprint,
            unicode_policy_version=self._policy.unicode_policy_version,
            text_bytes=text_bytes,
            attachment_count=attachment_count,
            error_code=error_code,
            retry_after_seconds=retry_after_seconds,
        )

    def _block(
        self,
        stage: GuardrailStage,
        error_code: GuardrailErrorCode,
        *,
        text_bytes: int | None = None,
        attachment_count: int,
        retry_after_seconds: int | None = None,
    ) -> None:
        raise GuardrailBlockedError(
            self._decision(
                stage=stage,
                outcome=GuardrailOutcome.BLOCKED,
                text_bytes=text_bytes,
                attachment_count=attachment_count,
                error_code=error_code,
                retry_after_seconds=retry_after_seconds,
            )
        )


class GuardedRunSubmissionService:
    """Make the guardrail the mandatory boundary before Run persistence."""

    def __init__(
        self,
        guardrail: InputGuardrailService,
        *,
        run_creator: RunCreator,
    ) -> None:
        self._guardrail = guardrail
        self._run_creator = run_creator

    async def submit(
        self,
        *,
        actor: AuthenticatedPrincipal,
        request: RunRequest,
        route_key: str,
    ) -> GuardedRunCreation:
        guarded = await self._guardrail.guard(actor=actor, request=request)
        creation = await self._run_creator.create_run(guarded.request, route_key=route_key)
        return GuardedRunCreation(creation=creation, decision=guarded.decision)
