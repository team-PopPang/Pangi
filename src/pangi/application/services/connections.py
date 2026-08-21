"""Deterministic construction of bounded Connection Tool Registry snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from math import isfinite

from pangi.application.contracts.connections import (
    ToolRegistryContractError,
    ToolRegistrySnapshot,
    ToolSchemaLimits,
)
from pangi.domain.connections import Connection, ConnectionToolState
from pangi.domain.tool_guardrails import ToolPermission


class ToolRegistrySnapshotFactory:
    def __init__(self, limits: ToolSchemaLimits | None = None) -> None:
        self._limits = limits or ToolSchemaLimits()

    def build(
        self,
        *,
        connection: Connection,
        stable_tool_id: str,
        remote_name: str,
        permission: ToolPermission,
        input_schema: Mapping[str, object],
        state: ConnectionToolState,
        discovered_at: datetime,
    ) -> ToolRegistrySnapshot:
        if not isinstance(connection, Connection):
            raise TypeError("connection must be a Connection")
        if not isinstance(input_schema, Mapping):
            raise ToolRegistryContractError("Tool schema must be a JSON object")
        try:
            counter = [0]
            normalized = _normalize_schema_value(
                input_schema,
                depth=0,
                active=set(),
                counter=counter,
                limits=self._limits,
            )
            if not isinstance(normalized, dict):
                raise TypeError
            canonical = json.dumps(
                normalized,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            encoded = canonical.encode("utf-8")
        except (RecursionError, TypeError, UnicodeEncodeError, ValueError):
            raise ToolRegistryContractError("Tool schema is not bounded JSON") from None
        if len(encoded) > self._limits.max_bytes:
            raise ToolRegistryContractError("Tool schema exceeds the canonical byte limit")
        return ToolRegistrySnapshot(
            stable_tool_id=stable_tool_id,
            connection_id=connection.id,
            remote_name=remote_name,
            connection_scope=connection.scope,
            connection_owner_user_id=connection.owner_user_id,
            permission=permission,
            state=state,
            canonical_schema_json=canonical,
            schema_fingerprint=hashlib.sha256(encoded).hexdigest(),
            discovered_at=discovered_at,
        )


def _normalize_schema_value(
    value: object,
    *,
    depth: int,
    active: set[int],
    counter: list[int],
    limits: ToolSchemaLimits,
) -> object:
    if depth > limits.max_depth:
        raise ValueError("schema depth exceeded")
    counter[0] += 1
    if counter[0] > limits.max_items:
        raise ValueError("schema item count exceeded")
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("non-finite number")
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic mapping")
        active.add(identity)
        try:
            normalized: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("schema keys must be strings")
                normalized[key] = _normalize_schema_value(
                    item,
                    depth=depth + 1,
                    active=active,
                    counter=counter,
                    limits=limits,
                )
            return normalized
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic sequence")
        active.add(identity)
        try:
            return [
                _normalize_schema_value(
                    item,
                    depth=depth + 1,
                    active=active,
                    counter=counter,
                    limits=limits,
                )
                for item in value
            ]
        finally:
            active.remove(identity)
    raise TypeError("schema value is not JSON compatible")
