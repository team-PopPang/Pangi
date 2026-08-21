"""Secret-safe Connection Tool Registry snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import NoReturn

from pangi.application.contracts.tool_guardrails import ResolvedTool
from pangi.domain.connections import ConnectionScope, ConnectionToolState
from pangi.domain.tool_guardrails import ToolPermission

MAX_TOOL_SCHEMA_DEPTH = 32
MAX_TOOL_SCHEMA_ITEMS = 10_000
MAX_TOOL_SCHEMA_BYTES = 1_000_000

_STABLE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_OPAQUE_IDENTIFIER = re.compile(r"^[!-~]{1,1024}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ToolRegistryContractError(ValueError):
    """A Tool Registry value violates a stable internal contract."""


@dataclass(frozen=True, slots=True)
class ToolSchemaLimits:
    max_depth: int = MAX_TOOL_SCHEMA_DEPTH
    max_items: int = MAX_TOOL_SCHEMA_ITEMS
    max_bytes: int = MAX_TOOL_SCHEMA_BYTES

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("max_depth", self.max_depth, MAX_TOOL_SCHEMA_DEPTH),
            ("max_items", self.max_items, MAX_TOOL_SCHEMA_ITEMS),
            ("max_bytes", self.max_bytes, MAX_TOOL_SCHEMA_BYTES),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ToolRegistryContractError(f"{field_name} must be an integer")
            if not 1 <= value <= maximum:
                raise ToolRegistryContractError(
                    f"{field_name} must be between 1 and {maximum}"
                )


@dataclass(frozen=True, slots=True)
class ToolRegistrySnapshot:
    stable_tool_id: str
    connection_id: str = field(repr=False)
    remote_name: str = field(repr=False)
    connection_scope: ConnectionScope
    permission: ToolPermission
    state: ConnectionToolState
    canonical_schema_json: str = field(repr=False)
    schema_fingerprint: str
    discovered_at: datetime
    connection_owner_user_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _validate_stable_identifier(self.stable_tool_id, field_name="stable_tool_id")
        _validate_opaque_identifier(self.connection_id, field_name="connection_id")
        _validate_opaque_identifier(self.remote_name, field_name="remote_name")
        try:
            object.__setattr__(
                self,
                "connection_scope",
                ConnectionScope(self.connection_scope),
            )
            object.__setattr__(self, "permission", ToolPermission(self.permission))
            object.__setattr__(self, "state", ConnectionToolState(self.state))
        except ValueError as error:
            raise ToolRegistryContractError(
                "Tool Registry contains an invalid enum value"
            ) from error
        if self.connection_scope is ConnectionScope.USER:
            if self.connection_owner_user_id is None:
                raise ToolRegistryContractError("a user-scoped Tool requires a connection owner")
            _validate_opaque_identifier(
                self.connection_owner_user_id,
                field_name="connection_owner_user_id",
            )
        elif self.connection_owner_user_id is not None:
            raise ToolRegistryContractError("an instance-scoped Tool cannot have a user owner")
        if not isinstance(self.canonical_schema_json, str):
            raise ToolRegistryContractError("canonical_schema_json must be text")
        try:
            encoded = self.canonical_schema_json.encode("utf-8")
        except UnicodeEncodeError:
            raise ToolRegistryContractError("Tool schema is not valid UTF-8 text") from None
        if not encoded or len(encoded) > MAX_TOOL_SCHEMA_BYTES:
            raise ToolRegistryContractError("Tool schema exceeds the canonical byte limit")
        try:
            schema = json.loads(
                self.canonical_schema_json,
                parse_constant=_reject_non_json_number,
            )
            if not isinstance(schema, dict):
                raise ValueError
            _validate_schema_limits(schema)
            canonical = json.dumps(
                schema,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
            raise ToolRegistryContractError("Tool schema is not canonical JSON") from None
        if canonical != self.canonical_schema_json:
            raise ToolRegistryContractError("Tool schema is not canonical JSON")
        if _SHA256.fullmatch(self.schema_fingerprint) is None:
            raise ToolRegistryContractError("schema_fingerprint must be a SHA-256 hex digest")
        if hashlib.sha256(encoded).hexdigest() != self.schema_fingerprint:
            raise ToolRegistryContractError("schema_fingerprint does not match the Tool schema")
        if self.discovered_at.tzinfo is None or self.discovered_at.utcoffset() is None:
            raise ToolRegistryContractError("discovered_at must be timezone-aware")
        object.__setattr__(self, "discovered_at", self.discovered_at.astimezone(UTC))

    def as_resolved_tool(self) -> ResolvedTool:
        return ResolvedTool(
            tool_id=self.stable_tool_id,
            connection_id=self.connection_id,
            tool_name=self.remote_name,
            connection_scope=self.connection_scope,
            connection_owner_user_id=self.connection_owner_user_id,
            permission=self.permission,
            schema_fingerprint=self.schema_fingerprint,
            active=self.state is ConnectionToolState.ACTIVE,
        )


def _validate_stable_identifier(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _STABLE_IDENTIFIER.fullmatch(value) is None:
        raise ToolRegistryContractError(f"{field_name} must be a stable identifier")


def _validate_opaque_identifier(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _OPAQUE_IDENTIFIER.fullmatch(value) is None:
        raise ToolRegistryContractError(f"{field_name} must be a bounded opaque identifier")


def _validate_schema_limits(schema: Mapping[str, object]) -> None:
    count = 0
    stack: list[tuple[object, int]] = [(schema, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > MAX_TOOL_SCHEMA_DEPTH:
            raise ToolRegistryContractError("Tool schema exceeds the depth limit")
        count += 1
        if count > MAX_TOOL_SCHEMA_ITEMS:
            raise ToolRegistryContractError("Tool schema exceeds the item limit")
        if isinstance(value, Mapping):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, Sequence) and not isinstance(value, str):
            stack.extend((item, depth + 1) for item in value)


def _reject_non_json_number(value: str) -> NoReturn:
    raise ValueError("non-JSON number")
