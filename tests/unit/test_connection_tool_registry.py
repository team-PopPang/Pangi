"""Bounded and secret-safe Tool Registry snapshot tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from pangi.application.contracts.connections import (
    ToolRegistryContractError,
    ToolSchemaLimits,
)
from pangi.application.services.connections import ToolRegistrySnapshotFactory
from pangi.domain.connections import (
    Connection,
    ConnectionAuthType,
    ConnectionScope,
    ConnectionState,
    ConnectionToolState,
    ConnectionTransport,
)
from pangi.domain.tool_guardrails import ToolPermission

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _connection() -> Connection:
    return Connection(
        id="connection-user-0001",
        kind="linear",
        display_name="Linear",
        scope=ConnectionScope.USER,
        owner_user_id="member-user-00001",
        transport=ConnectionTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.example.test",
        auth_type=ConnectionAuthType.OAUTH,
        secret_ref="secret://connection-user-0001/oauth",
        state=ConnectionState.CONNECTED,
        created_at=NOW,
        updated_at=NOW,
        connected_at=NOW,
        last_checked_at=NOW,
    )


def _snapshot(
    schema: dict[str, object],
    *,
    state: ConnectionToolState = ConnectionToolState.ACTIVE,
    factory: ToolRegistrySnapshotFactory | None = None,
):
    return (factory or ToolRegistrySnapshotFactory()).build(
        connection=_connection(),
        stable_tool_id="linear.issue.create",
        remote_name="create_issue",
        permission=ToolPermission.WRITE,
        input_schema=schema,
        state=state,
        discovered_at=NOW,
    )


def test_schema_fingerprint_is_deterministic_across_object_key_order() -> None:
    first = _snapshot(
        {
            "type": "object",
            "required": ["title"],
            "properties": {"title": {"type": "string", "maxLength": 200}},
        }
    )
    second = _snapshot(
        {
            "properties": {"title": {"maxLength": 200, "type": "string"}},
            "required": ["title"],
            "type": "object",
        }
    )

    assert first.canonical_schema_json == second.canonical_schema_json
    assert first.schema_fingerprint == second.schema_fingerprint


@pytest.mark.parametrize(
    ("state", "active"),
    (
        (ConnectionToolState.NEW, False),
        (ConnectionToolState.ACTIVE, True),
        (ConnectionToolState.CHANGED, False),
        (ConnectionToolState.UNAVAILABLE, False),
    ),
)
def test_registry_snapshot_maps_losslessly_to_guardrail_resolution(
    state: ConnectionToolState,
    active: bool,
) -> None:
    snapshot = _snapshot({"type": "object"}, state=state)
    resolved = snapshot.as_resolved_tool()

    assert resolved.tool_id == snapshot.stable_tool_id
    assert resolved.connection_id == snapshot.connection_id
    assert resolved.tool_name == snapshot.remote_name
    assert resolved.connection_scope is snapshot.connection_scope
    assert resolved.connection_owner_user_id == snapshot.connection_owner_user_id
    assert resolved.permission is snapshot.permission
    assert resolved.schema_fingerprint == snapshot.schema_fingerprint
    assert resolved.active is active


def test_registry_repr_hides_connection_remote_name_schema_and_owner() -> None:
    secret_marker = "schema-secret-marker"
    snapshot = _snapshot(
        {
            "type": "object",
            "description": secret_marker,
        }
    )
    rendered = repr(snapshot)

    assert "connection-user-0001" not in rendered
    assert "create_issue" not in rendered
    assert "member-user-00001" not in rendered
    assert secret_marker not in rendered


def test_registry_rejects_non_json_values_and_cycles_without_echoing_input() -> None:
    factory = ToolRegistrySnapshotFactory()
    secret_marker = "schema-value-do-not-expose"
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    invalid_schemas: tuple[dict[object, object], ...] = (
        {"type": object()},
        {1: secret_marker},
        {"minimum": float("nan")},
        {"description": "\ud800"},
        cyclic,
    )
    for schema in invalid_schemas:
        with pytest.raises(ToolRegistryContractError) as captured:
            factory.build(
                connection=_connection(),
                stable_tool_id="linear.issue.create",
                remote_name="create_issue",
                permission=ToolPermission.WRITE,
                input_schema=cast(dict[str, object], schema),
                state=ConnectionToolState.NEW,
                discovered_at=NOW,
            )
        assert secret_marker not in str(captured.value)


@pytest.mark.parametrize(
    ("limits", "schema"),
    (
        (ToolSchemaLimits(max_depth=1), {"properties": {"title": {"type": "string"}}}),
        (ToolSchemaLimits(max_items=2), {"type": "object", "title": "value"}),
        (ToolSchemaLimits(max_bytes=20), {"description": "x" * 100}),
    ),
)
def test_registry_enforces_depth_item_and_byte_limits(
    limits: ToolSchemaLimits,
    schema: dict[str, object],
) -> None:
    with pytest.raises(ToolRegistryContractError):
        _snapshot(schema, factory=ToolRegistrySnapshotFactory(limits))


def test_registry_snapshot_rejects_a_fingerprint_that_does_not_match_schema() -> None:
    snapshot = _snapshot({"type": "object"})

    with pytest.raises(ToolRegistryContractError, match="does not match"):
        type(snapshot)(
            stable_tool_id=snapshot.stable_tool_id,
            connection_id=snapshot.connection_id,
            remote_name=snapshot.remote_name,
            connection_scope=snapshot.connection_scope,
            connection_owner_user_id=snapshot.connection_owner_user_id,
            permission=snapshot.permission,
            state=snapshot.state,
            canonical_schema_json=snapshot.canonical_schema_json,
            schema_fingerprint="f" * 64,
            discovered_at=snapshot.discovered_at,
        )
