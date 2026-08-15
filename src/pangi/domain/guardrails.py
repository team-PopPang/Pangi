"""Framework-free trust and guardrail decision values."""

from enum import StrEnum


class TrustLevel(StrEnum):
    """Trust assigned to data at a Pangi boundary."""

    TRUSTED = "trusted"
    CONDITIONALLY_TRUSTED = "conditionally_trusted"
    UNTRUSTED = "untrusted"
    SECRET = "secret"


class GuardrailStage(StrEnum):
    """Stable input-guardrail stages in evaluation order."""

    PRINCIPAL = "principal"
    NORMALIZATION = "normalization"
    TEXT = "text"
    ATTACHMENTS = "attachments"
    EXPLICIT_SKILL = "explicit_skill"
    RATE_LIMIT = "rate_limit"
    COMPLETE = "complete"


class GuardrailOutcome(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class GuardrailErrorCode(StrEnum):
    """Secret-safe, stable reasons for rejecting an untrusted request."""

    PRINCIPAL_INACTIVE = "principal_inactive"
    PRINCIPAL_ID_MISMATCH = "principal_id_mismatch"
    PRINCIPAL_ROLE_MISMATCH = "principal_role_mismatch"
    UNSAFE_UNICODE = "unsafe_unicode"
    TEXT_BYTES_EXCEEDED = "text_bytes_exceeded"
    ATTACHMENT_COUNT_EXCEEDED = "attachment_count_exceeded"
    ATTACHMENT_METADATA_MISSING = "attachment_metadata_missing"
    ATTACHMENT_BYTES_EXCEEDED = "attachment_bytes_exceeded"
    ATTACHMENT_TOTAL_BYTES_EXCEEDED = "attachment_total_bytes_exceeded"
    ATTACHMENT_MEDIA_TYPE_DENIED = "attachment_media_type_denied"
    EXPLICIT_SKILL_DENIED = "explicit_skill_denied"
    EXPLICIT_SKILL_UNAVAILABLE = "explicit_skill_unavailable"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
