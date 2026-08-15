"""Framework-free immutable Audit Event contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_RESOURCE_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,119}$")


class AuditOutcome(StrEnum):
    """Stable outcomes shared by successful and rejected management actions."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"


class AuditContractError(ValueError):
    """An Audit value violates the immutable public contract."""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One append-only, already-redacted management event."""

    id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    outcome: AuditOutcome
    metadata: Mapping[str, object] = field(repr=False)
    created_at: datetime

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.id, "audit event id"),
            (self.actor_id, "audit actor id"),
            (self.resource_id, "audit resource id"),
        ):
            if _IDENTIFIER.fullmatch(value) is None:
                raise AuditContractError(
                    f"{field_name} must contain 1-255 stable identifier characters"
                )
        if _NAMESPACE.fullmatch(self.action) is None:
            raise AuditContractError(
                "audit action must be a lowercase namespaced identifier"
            )
        if _RESOURCE_TYPE.fullmatch(self.resource_type) is None:
            raise AuditContractError(
                "audit resource_type must be a lowercase identifier"
            )
        try:
            object.__setattr__(self, "outcome", AuditOutcome(self.outcome))
        except ValueError as error:
            raise AuditContractError("audit outcome is invalid") from error
        if not isinstance(self.metadata, Mapping):
            raise AuditContractError("audit metadata must be a mapping")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise AuditContractError("audit created_at must be timezone-aware")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value
