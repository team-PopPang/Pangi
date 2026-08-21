"""Connection contract and lifecycle state machine tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from pangi.domain.connections import (
    Connection,
    ConnectionAuthType,
    ConnectionContractError,
    ConnectionErrorCode,
    ConnectionScope,
    ConnectionState,
    ConnectionTransport,
    InvalidConnectionTransitionError,
    allowed_connection_transitions,
    transition_connection,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)

CONNECTION_EDGES = {
    (ConnectionState.DISCONNECTED, ConnectionState.CONNECTING),
    (ConnectionState.CONNECTING, ConnectionState.CONNECTED),
    (ConnectionState.CONNECTING, ConnectionState.ERROR),
    (ConnectionState.CONNECTING, ConnectionState.DISCONNECTED),
    (ConnectionState.CONNECTED, ConnectionState.DEGRADED),
    (ConnectionState.CONNECTED, ConnectionState.DISCONNECTED),
    (ConnectionState.DEGRADED, ConnectionState.CONNECTED),
    (ConnectionState.DEGRADED, ConnectionState.ERROR),
    (ConnectionState.DEGRADED, ConnectionState.DISCONNECTED),
    (ConnectionState.ERROR, ConnectionState.CONNECTING),
    (ConnectionState.ERROR, ConnectionState.DISCONNECTED),
}


def _connection(
    state: ConnectionState = ConnectionState.DISCONNECTED,
    **changes: object,
) -> Connection:
    values: dict[str, object] = {
        "id": "connection-user-0001",
        "kind": "linear",
        "display_name": "Linear",
        "display_qualifier": "Engineering",
        "scope": ConnectionScope.USER,
        "owner_user_id": "member-user-00001",
        "transport": ConnectionTransport.STREAMABLE_HTTP,
        "endpoint": "https://mcp.example.test",
        "auth_type": ConnectionAuthType.OAUTH,
        "secret_ref": "secret://connection-user-0001/oauth",
        "state": state,
        "created_at": NOW,
        "updated_at": NOW,
        "connected_at": (
            NOW
            if state in {ConnectionState.CONNECTED, ConnectionState.DEGRADED, ConnectionState.ERROR}
            else None
        ),
        "last_checked_at": (
            NOW
            if state in {ConnectionState.CONNECTED, ConnectionState.DEGRADED, ConnectionState.ERROR}
            else None
        ),
        "last_error_code": (
            "connection_probe_failed"
            if state in {ConnectionState.DEGRADED, ConnectionState.ERROR}
            else None
        ),
    }
    values.update(changes)
    return Connection(**values)  # type: ignore[arg-type]


def test_user_and_instance_connection_scope_invariants() -> None:
    user = _connection()
    instance = _connection(
        id="connection-instance-0001",
        scope=ConnectionScope.INSTANCE,
        owner_user_id=None,
    )

    assert user.owner_user_id == "member-user-00001"
    assert instance.owner_user_id is None

    with pytest.raises(ConnectionContractError, match="requires an owner"):
        _connection(owner_user_id=None)
    with pytest.raises(ConnectionContractError, match="cannot have a user owner"):
        _connection(scope=ConnectionScope.INSTANCE)


def test_transport_and_auth_shapes_are_mutually_exclusive() -> None:
    stdio = _connection(
        id="connection-instance-stdio",
        scope=ConnectionScope.INSTANCE,
        owner_user_id=None,
        transport=ConnectionTransport.STDIO,
        endpoint=None,
        command="/opt/pangi/connectors/linear-mcp",
        args=("--stdio",),
        auth_type=ConnectionAuthType.ENVIRONMENT,
    )

    assert stdio.args == ("--stdio",)

    with pytest.raises(ConnectionContractError, match="stdio requires only a command"):
        _connection(
            transport=ConnectionTransport.STDIO,
            command="/opt/mcp",
        )
    with pytest.raises(ConnectionContractError, match="requires only an endpoint"):
        _connection(command="/opt/mcp")
    with pytest.raises(ConnectionContractError, match="environment auth requires stdio"):
        _connection(auth_type=ConnectionAuthType.ENVIRONMENT)
    with pytest.raises(ConnectionContractError, match="remote auth requires"):
        replace(stdio, auth_type=ConnectionAuthType.OAUTH)


def test_connection_state_machine_accepts_only_declared_edges() -> None:
    at = NOW + timedelta(seconds=1)
    for current in ConnectionState:
        expected = frozenset(target for source, target in CONNECTION_EDGES if source is current)
        assert allowed_connection_transitions(current) == expected
        for target in ConnectionState:
            connection = _connection(current)
            error_code = (
                "connection_probe_failed"
                if target in {ConnectionState.DEGRADED, ConnectionState.ERROR}
                else None
            )
            if (current, target) in CONNECTION_EDGES:
                changed = transition_connection(
                    connection,
                    target,
                    at=at,
                    error_code=error_code,
                )
                assert changed.state is target
                assert changed.revision == 1
                assert changed.updated_at == at
                if target is ConnectionState.CONNECTED:
                    assert changed.connected_at is not None
                    assert changed.last_error_code is None
                if target in {ConnectionState.DEGRADED, ConnectionState.ERROR}:
                    assert changed.last_checked_at == at
                    assert changed.last_error_code == "connection_probe_failed"
            else:
                with pytest.raises(InvalidConnectionTransitionError) as captured:
                    transition_connection(
                        connection,
                        target,
                        at=at,
                        error_code=error_code,
                    )
                assert captured.value.code is ConnectionErrorCode.INVALID_STATE_TRANSITION


def test_transition_requires_safe_error_metadata_and_monotonic_time() -> None:
    connected = _connection(ConnectionState.CONNECTED)

    with pytest.raises(ConnectionContractError, match="error code"):
        transition_connection(connected, ConnectionState.DEGRADED, at=NOW + timedelta(seconds=1))
    with pytest.raises(ConnectionContractError, match="only degraded or error"):
        transition_connection(
            connected,
            ConnectionState.DISCONNECTED,
            at=NOW + timedelta(seconds=1),
            error_code="not_allowed",
        )
    with pytest.raises(ConnectionContractError, match="cannot precede"):
        transition_connection(
            connected,
            ConnectionState.DISCONNECTED,
            at=NOW - timedelta(seconds=1),
        )


def test_connection_repr_and_errors_do_not_expose_sensitive_configuration() -> None:
    connection = _connection()
    rendered = repr(connection)

    assert "https://mcp.example.test" not in rendered
    assert "secret://connection-user-0001/oauth" not in rendered
    assert "member-user-00001" not in rendered

    sensitive_command = "/opt/mcp --token=do-not-expose"
    with pytest.raises(ConnectionContractError) as captured:
        _connection(
            transport=ConnectionTransport.STDIO,
            endpoint=None,
            command=sensitive_command,
            args=(),
            auth_type=ConnectionAuthType.OAUTH,
        )
    assert sensitive_command not in str(captured.value)


def test_connection_rejects_inconsistent_state_metadata() -> None:
    with pytest.raises(ConnectionContractError, match="connected_at"):
        _connection(ConnectionState.CONNECTED, connected_at=None)
    with pytest.raises(ConnectionContractError, match="error code"):
        _connection(ConnectionState.ERROR, last_error_code=None)
    with pytest.raises(ConnectionContractError, match="only degraded or error"):
        _connection(ConnectionState.DISCONNECTED, last_error_code="stale_error")

