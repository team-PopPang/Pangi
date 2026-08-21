"""Framework-free Connection values and lifecycle transition rules."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from pangi.domain.tool_guardrails import ToolConnectionScope

ConnectionScope = ToolConnectionScope

_STABLE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_OPAQUE_IDENTIFIER = re.compile(r"^[!-~]{1,1024}$")


class ConnectionTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class ConnectionAuthType(StrEnum):
    NONE = "none"
    OAUTH = "oauth"
    BEARER = "bearer"
    ENVIRONMENT = "environment"


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    ERROR = "error"


class ConnectionToolState(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    CHANGED = "changed"
    UNAVAILABLE = "unavailable"


class ConnectionErrorCode(StrEnum):
    INVALID_STATE_TRANSITION = "connection_invalid_state_transition"


class ConnectionContractError(ValueError):
    """A Connection value violates a stable internal contract."""


class InvalidConnectionTransitionError(RuntimeError):
    code = ConnectionErrorCode.INVALID_STATE_TRANSITION

    def __init__(self, current: ConnectionState, target: ConnectionState) -> None:
        super().__init__(f"Connection cannot transition from {current.value} to {target.value}")
        self.current = current
        self.target = target


@dataclass(frozen=True, slots=True)
class Connection:
    id: str
    kind: str
    display_name: str
    scope: ConnectionScope
    transport: ConnectionTransport
    auth_type: ConnectionAuthType
    state: ConnectionState
    created_at: datetime
    updated_at: datetime
    display_qualifier: str | None = None
    owner_user_id: str | None = field(default=None, repr=False)
    endpoint: str | None = field(default=None, repr=False)
    command: str | None = field(default=None, repr=False)
    args: tuple[str, ...] = field(default=(), repr=False)
    secret_ref: str | None = field(default=None, repr=False)
    connected_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_error_code: str | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        _validate_stable_identifier(self.id, field_name="connection id")
        _validate_stable_identifier(self.kind, field_name="connection kind")
        _validate_text(self.display_name, field_name="display_name", limit=120)
        if self.display_qualifier is not None:
            _validate_text(
                self.display_qualifier,
                field_name="display_qualifier",
                limit=120,
            )
        try:
            object.__setattr__(self, "scope", ConnectionScope(self.scope))
            object.__setattr__(self, "transport", ConnectionTransport(self.transport))
            object.__setattr__(self, "auth_type", ConnectionAuthType(self.auth_type))
            object.__setattr__(self, "state", ConnectionState(self.state))
        except ValueError as error:
            raise ConnectionContractError("Connection contains an invalid enum value") from error
        _validate_scope(self.scope, self.owner_user_id)
        _validate_transport(
            self.transport,
            endpoint=self.endpoint,
            command=self.command,
            args=self.args,
        )
        _validate_auth_transport(self.auth_type, self.transport)
        if self.secret_ref is not None:
            _validate_opaque_identifier(self.secret_ref, field_name="secret_ref")
        if self.auth_type is ConnectionAuthType.NONE and self.secret_ref is not None:
            raise ConnectionContractError("an unauthenticated Connection cannot reference a Secret")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise ConnectionContractError("revision must be an integer")
        if self.revision < 0:
            raise ConnectionContractError("revision cannot be negative")
        created_at = _utc(self.created_at, field_name="created_at")
        updated_at = _utc(self.updated_at, field_name="updated_at")
        if updated_at < created_at:
            raise ConnectionContractError("updated_at cannot precede created_at")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        for field_name in ("connected_at", "last_checked_at"):
            value = getattr(self, field_name)
            if value is None:
                continue
            normalized = _utc(value, field_name=field_name)
            if normalized < created_at or normalized > updated_at:
                raise ConnectionContractError(
                    f"{field_name} must be between created_at and updated_at"
                )
            object.__setattr__(self, field_name, normalized)
        if self.connected_at is not None and self.last_checked_at is not None:
            if self.last_checked_at < self.connected_at:
                raise ConnectionContractError("last_checked_at cannot precede connected_at")
        _validate_state_metadata(
            self.state,
            connected_at=self.connected_at,
            last_error_code=self.last_error_code,
        )


_CONNECTION_TRANSITIONS: Mapping[ConnectionState, frozenset[ConnectionState]] = (
    MappingProxyType(
        {
            ConnectionState.DISCONNECTED: frozenset({ConnectionState.CONNECTING}),
            ConnectionState.CONNECTING: frozenset(
                {
                    ConnectionState.CONNECTED,
                    ConnectionState.ERROR,
                    ConnectionState.DISCONNECTED,
                }
            ),
            ConnectionState.CONNECTED: frozenset(
                {ConnectionState.DEGRADED, ConnectionState.DISCONNECTED}
            ),
            ConnectionState.DEGRADED: frozenset(
                {
                    ConnectionState.CONNECTED,
                    ConnectionState.ERROR,
                    ConnectionState.DISCONNECTED,
                }
            ),
            ConnectionState.ERROR: frozenset(
                {ConnectionState.CONNECTING, ConnectionState.DISCONNECTED}
            ),
        }
    )
)


def allowed_connection_transitions(state: ConnectionState) -> frozenset[ConnectionState]:
    return _CONNECTION_TRANSITIONS[state]


def transition_connection(
    connection: Connection,
    target: ConnectionState,
    *,
    at: datetime,
    error_code: str | None = None,
) -> Connection:
    try:
        normalized_target = ConnectionState(target)
    except ValueError as error:
        raise ConnectionContractError("target contains an invalid Connection state") from error
    if normalized_target not in allowed_connection_transitions(connection.state):
        raise InvalidConnectionTransitionError(connection.state, normalized_target)
    timestamp = _utc(at, field_name="transition timestamp")
    if timestamp < connection.updated_at:
        raise ConnectionContractError("transition timestamp cannot precede updated_at")
    if normalized_target in {ConnectionState.DEGRADED, ConnectionState.ERROR}:
        _validate_error_code(error_code)
    elif error_code is not None:
        raise ConnectionContractError("only degraded or error transitions accept an error code")
    connected_at = connection.connected_at
    if normalized_target is ConnectionState.CONNECTED and connected_at is None:
        connected_at = timestamp
    last_checked_at = (
        timestamp
        if normalized_target
        in {ConnectionState.CONNECTED, ConnectionState.DEGRADED, ConnectionState.ERROR}
        else connection.last_checked_at
    )
    return replace(
        connection,
        state=normalized_target,
        updated_at=timestamp,
        connected_at=connected_at,
        last_checked_at=last_checked_at,
        last_error_code=(
            error_code
            if normalized_target in {ConnectionState.DEGRADED, ConnectionState.ERROR}
            else None
        ),
        revision=connection.revision + 1,
    )


def _validate_stable_identifier(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _STABLE_IDENTIFIER.fullmatch(value) is None:
        raise ConnectionContractError(f"{field_name} must be a stable identifier")


def _validate_opaque_identifier(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _OPAQUE_IDENTIFIER.fullmatch(value) is None:
        raise ConnectionContractError(f"{field_name} must be a bounded opaque identifier")


def _validate_text(value: object, *, field_name: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ConnectionContractError(
            f"{field_name} must contain 1-{limit} non-blank characters"
        )


def _validate_scope(scope: ConnectionScope, owner_user_id: str | None) -> None:
    if scope is ConnectionScope.USER:
        if owner_user_id is None:
            raise ConnectionContractError("a user-scoped Connection requires an owner")
        _validate_stable_identifier(owner_user_id, field_name="owner_user_id")
    elif owner_user_id is not None:
        raise ConnectionContractError("an instance-scoped Connection cannot have a user owner")


def _validate_transport(
    transport: ConnectionTransport,
    *,
    endpoint: str | None,
    command: str | None,
    args: tuple[str, ...],
) -> None:
    if not isinstance(args, tuple):
        raise ConnectionContractError("stdio args must be an immutable tuple")
    if len(args) > 64:
        raise ConnectionContractError("stdio args must contain at most 64 values")
    for argument in args:
        _validate_text(argument, field_name="stdio argument", limit=4_096)
    if transport is ConnectionTransport.STDIO:
        if command is None or endpoint is not None:
            raise ConnectionContractError("stdio requires only a command")
        _validate_text(command, field_name="stdio command", limit=4_096)
        return
    if endpoint is None or command is not None or args:
        raise ConnectionContractError("Streamable HTTP requires only an endpoint")
    _validate_text(endpoint, field_name="Streamable HTTP endpoint", limit=2_048)


def _validate_auth_transport(
    auth_type: ConnectionAuthType,
    transport: ConnectionTransport,
) -> None:
    if auth_type is ConnectionAuthType.ENVIRONMENT and transport is not ConnectionTransport.STDIO:
        raise ConnectionContractError("environment auth requires stdio transport")
    if auth_type in {ConnectionAuthType.OAUTH, ConnectionAuthType.BEARER}:
        if transport is not ConnectionTransport.STREAMABLE_HTTP:
            raise ConnectionContractError("remote auth requires Streamable HTTP transport")


def _validate_state_metadata(
    state: ConnectionState,
    *,
    connected_at: datetime | None,
    last_error_code: str | None,
) -> None:
    if state in {ConnectionState.CONNECTED, ConnectionState.DEGRADED}:
        if connected_at is None:
            raise ConnectionContractError("connected states require connected_at")
    if state in {ConnectionState.DEGRADED, ConnectionState.ERROR}:
        _validate_error_code(last_error_code)
    elif last_error_code is not None:
        raise ConnectionContractError("only degraded or error states can contain an error code")


def _validate_error_code(value: str | None) -> None:
    if value is None or _STABLE_IDENTIFIER.fullmatch(value) is None:
        raise ConnectionContractError("Connection error code must be a stable identifier")


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConnectionContractError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
