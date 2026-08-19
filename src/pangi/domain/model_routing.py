"""Framework-free Model routing values and stable failure reasons."""

from enum import StrEnum


class DataClass(StrEnum):
    """Sensitivity classes ordered from least to most restrictive."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    PERSONAL = "personal"
    RESTRICTED = "restricted"


_DATA_CLASS_ORDER = (
    DataClass.PUBLIC,
    DataClass.INTERNAL,
    DataClass.CONFIDENTIAL,
    DataClass.PERSONAL,
    DataClass.RESTRICTED,
)


def data_class_rank(value: DataClass) -> int:
    """Return the stable sensitivity rank used for aggregate classification."""

    return _DATA_CLASS_ORDER.index(value)


class ModelPurpose(StrEnum):
    ORCHESTRATION = "orchestration"
    SUBAGENT = "subagent"
    SKILL = "skill"
    EVAL = "eval"
    RED_TEAM = "red_team"


class ModelMessageRole(StrEnum):
    """Portable message roles supported by every built-in Model Provider."""

    SYSTEM = "system"
    USER = "user"


class ModelRetention(StrEnum):
    PROVIDER_DEFAULT = "provider_default"
    ZERO_RETENTION = "zero_retention"


class ModelPolicyStage(StrEnum):
    POLICY = "policy"
    CLASSIFICATION = "classification"
    CANDIDATE = "candidate"
    REDACTION = "redaction"
    COMPLETE = "complete"


class ModelPolicyOutcome(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class ModelPolicyErrorCode(StrEnum):
    """Secret-safe reasons for rejecting a proposed Model call."""

    POLICY_MISSING = "model_policy_missing"
    POLICY_DENIED = "model_policy_denied"
    REDACTION_FAILED = "model_redaction_failed"


class ModelProviderErrorCode(StrEnum):
    """Normalized Provider failures without SDK-specific details."""

    UNAVAILABLE = "model_provider_unavailable"
    RATE_LIMITED = "model_provider_rate_limited"
    TIMEOUT = "model_provider_timeout"
    AUTHENTICATION = "model_provider_authentication"
    INVALID_REQUEST = "model_provider_invalid_request"
    CONTENT_FILTERED = "model_provider_content_filtered"
    INVALID_STRUCTURED_OUTPUT = "model_invalid_structured_output"
    UNKNOWN = "model_provider_unknown"


class ModelFinishReason(StrEnum):
    """Provider-neutral reasons for a Model response ending."""

    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTERED = "content_filtered"
    UNKNOWN = "unknown"


class ModelInvocationState(StrEnum):
    """Persisted lifecycle states for one logical Model call."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"


class ModelPolicyState(StrEnum):
    """Lifecycle states for one immutable Model Policy version."""

    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"
