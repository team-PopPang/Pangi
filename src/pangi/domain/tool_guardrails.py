"""Framework-free Tool guardrail values and stable rejection reasons."""

from enum import StrEnum


class ToolPermission(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ToolApprovalRequirement(StrEnum):
    NONE = "none"
    USER = "user"
    ADMIN = "admin"


class ToolConnectionScope(StrEnum):
    USER = "user"
    INSTANCE = "instance"


class ToolPolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class ToolPolicyState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class ToolApprovalState(StrEnum):
    ACTIVE = "active"
    CONSUMED = "consumed"


class ToolApprovalConsumptionStatus(StrEnum):
    CONSUMED = "consumed"
    INVALID = "invalid"
    EXPIRED = "expired"


class ToolGuardrailStage(StrEnum):
    PRINCIPAL = "principal"
    RESOLUTION = "resolution"
    SCOPE = "scope"
    POLICY = "policy"
    ARGUMENTS = "arguments"
    APPROVAL = "approval"
    BUDGET = "budget"
    COMPLETE = "complete"


class ToolGuardrailOutcome(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class ToolGuardrailErrorCode(StrEnum):
    """Secret-safe, stable reasons for rejecting a proposed Tool call."""

    PRINCIPAL_INACTIVE = "tool_principal_inactive"
    PRINCIPAL_ID_MISMATCH = "tool_principal_id_mismatch"
    UNKNOWN_TOOL = "tool_unknown"
    TOOL_UNAVAILABLE = "tool_unavailable"
    CONNECTION_SCOPE_DENIED = "tool_connection_scope_denied"
    POLICY_MISSING = "tool_policy_missing"
    POLICY_CHANGED = "tool_policy_changed"
    POLICY_DENIED = "tool_policy_denied"
    PERMISSION_MISMATCH = "tool_permission_mismatch"
    SCHEMA_FINGERPRINT_MISMATCH = "tool_schema_fingerprint_mismatch"
    ARGUMENTS_NOT_JSON = "tool_arguments_not_json"
    ARGUMENT_BYTES_EXCEEDED = "tool_argument_bytes_exceeded"
    ARGUMENT_SCHEMA_INVALID = "tool_argument_schema_invalid"
    APPROVAL_REQUIRED = "tool_approval_required"
    APPROVAL_INVALID = "tool_approval_invalid"
    APPROVAL_EXPIRED = "tool_approval_expired"
    CALL_BUDGET_EXCEEDED = "tool_call_budget_exceeded"
