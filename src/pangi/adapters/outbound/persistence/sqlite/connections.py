"""SQLite persistence and WBS-06 resolution for Connection Tool Registry."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime

import aiosqlite

from pangi.adapters.outbound.persistence.sqlite.connection import fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.application.contracts.connections import ToolRegistrySnapshot
from pangi.application.contracts.tool_guardrails import ResolvedTool
from pangi.application.ports.connections import (
    ConnectionRegistryConflictError,
    ConnectionRegistryNotFoundError,
    ConnectionRegistryPersistenceError,
)
from pangi.domain.connections import (
    Connection,
    ConnectionAuthType,
    ConnectionScope,
    ConnectionState,
    ConnectionToolState,
    ConnectionTransport,
)
from pangi.domain.secrets import SecretReference
from pangi.domain.tool_guardrails import ToolPermission

_CONNECTION_COLUMNS = """
    id,
    kind,
    display_name,
    display_qualifier,
    scope,
    owner_user_id,
    transport,
    auth_type,
    state,
    config_json,
    secret_ref,
    connected_at,
    last_checked_at,
    last_error_code,
    revision,
    created_at,
    updated_at
"""

_TOOL_COLUMNS = """
    ct.stable_tool_id,
    ct.connection_id,
    ct.remote_name,
    ct.permission,
    ct.schema_json,
    ct.schema_fingerprint,
    ct.state AS tool_state,
    ct.discovered_at,
    c.scope AS connection_scope,
    c.owner_user_id AS connection_owner_user_id,
    c.state AS connection_state
"""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _config_json(connection: Connection) -> str:
    return _canonical_json(
        {
            "args": list(connection.args),
            "command": connection.command,
            "endpoint": connection.endpoint,
            "env_secret_refs": {
                name: reference.value
                for name, reference in connection.env_secret_refs.items()
            },
            "schema_version": 2,
        }
    )


def _required_datetime(row: aiosqlite.Row, name: str) -> datetime:
    value = datetime.fromisoformat(str(row[name]))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} is not timezone-aware")
    return value.astimezone(UTC)


def _optional_datetime(row: aiosqlite.Row, name: str) -> datetime | None:
    return None if row[name] is None else _required_datetime(row, name)


def _optional_text(row: aiosqlite.Row, name: str) -> str | None:
    return None if row[name] is None else str(row[name])


def _connection_from_row(row: aiosqlite.Row) -> Connection:
    config = json.loads(str(row["config_json"]))
    expected_keys = {
        "args",
        "command",
        "endpoint",
        "env_secret_refs",
        "schema_version",
    }
    if not isinstance(config, dict) or set(config) != expected_keys:
        raise ValueError("persisted Connection config has an invalid shape")
    if config["schema_version"] != 2:
        raise ValueError("persisted Connection config uses an unsupported schema")
    if _canonical_json(config) != str(row["config_json"]):
        raise ValueError("persisted Connection config is not canonical")
    args = config["args"]
    command = config["command"]
    endpoint = config["endpoint"]
    raw_environment_references = config["env_secret_refs"]
    if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
        raise ValueError("persisted Connection args are invalid")
    if command is not None and not isinstance(command, str):
        raise ValueError("persisted Connection command is invalid")
    if endpoint is not None and not isinstance(endpoint, str):
        raise ValueError("persisted Connection endpoint is invalid")
    if not isinstance(raw_environment_references, dict) or any(
        not isinstance(name, str) or not isinstance(reference, str)
        for name, reference in raw_environment_references.items()
    ):
        raise ValueError("persisted Connection environment references are invalid")
    environment_references = {
        name: SecretReference.parse(reference)
        for name, reference in raw_environment_references.items()
    }
    return Connection(
        id=str(row["id"]),
        kind=str(row["kind"]),
        display_name=str(row["display_name"]),
        display_qualifier=_optional_text(row, "display_qualifier"),
        scope=ConnectionScope(str(row["scope"])),
        owner_user_id=_optional_text(row, "owner_user_id"),
        transport=ConnectionTransport(str(row["transport"])),
        endpoint=endpoint,
        command=command,
        args=tuple(args),
        env_secret_refs=environment_references,
        auth_type=ConnectionAuthType(str(row["auth_type"])),
        secret_ref=_optional_text(row, "secret_ref"),
        state=ConnectionState(str(row["state"])),
        connected_at=_optional_datetime(row, "connected_at"),
        last_checked_at=_optional_datetime(row, "last_checked_at"),
        last_error_code=_optional_text(row, "last_error_code"),
        revision=int(row["revision"]),
        created_at=_required_datetime(row, "created_at"),
        updated_at=_required_datetime(row, "updated_at"),
    )


def _tool_snapshot_from_row(row: aiosqlite.Row) -> ToolRegistrySnapshot:
    return ToolRegistrySnapshot(
        stable_tool_id=str(row["stable_tool_id"]),
        connection_id=str(row["connection_id"]),
        remote_name=str(row["remote_name"]),
        connection_scope=ConnectionScope(str(row["connection_scope"])),
        connection_owner_user_id=_optional_text(row, "connection_owner_user_id"),
        permission=ToolPermission(str(row["permission"])),
        state=ConnectionToolState(str(row["tool_state"])),
        canonical_schema_json=str(row["schema_json"]),
        schema_fingerprint=str(row["schema_fingerprint"]),
        discovered_at=_required_datetime(row, "discovered_at"),
    )


def _connection_values(connection: Connection) -> tuple[object, ...]:
    return (
        connection.kind,
        connection.display_name,
        connection.display_qualifier,
        connection.scope.value,
        connection.owner_user_id,
        connection.transport.value,
        connection.auth_type.value,
        connection.state.value,
        _config_json(connection),
        connection.secret_ref,
        None if connection.connected_at is None else connection.connected_at.isoformat(),
        None if connection.last_checked_at is None else connection.last_checked_at.isoformat(),
        connection.last_error_code,
        connection.revision,
        connection.created_at.isoformat(),
        connection.updated_at.isoformat(),
    )


class SqliteConnectionRegistry:
    """Persist Connections and resolve globally stable Tool snapshots."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    @asynccontextmanager
    async def _runtime(self) -> AsyncIterator[None]:
        started_here = not self._database.started
        if started_here:
            await self._database.start()
        try:
            yield
        finally:
            if started_here:
                await self._database.close()

    async def add_connection(self, connection: Connection) -> None:
        if not isinstance(connection, Connection):
            raise TypeError("connection must be a Connection")
        if connection.revision != 0:
            raise ConnectionRegistryConflictError(
                "A new Connection must begin at revision zero"
            )
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "INSERT INTO connections "
                    "(id, kind, display_name, display_qualifier, scope, owner_user_id, "
                    "transport, auth_type, state, config_json, secret_ref, connected_at, "
                    "last_checked_at, last_error_code, revision, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (connection.id, *_connection_values(connection)),
                )
                await unit_of_work.commit()
        except aiosqlite.IntegrityError as error:
            raise ConnectionRegistryConflictError(
                "Connection could not be inserted"
            ) from error
        except aiosqlite.Error as error:
            raise ConnectionRegistryPersistenceError(
                "Connection could not be persisted"
            ) from error

    async def get_connection(self, connection_id: str) -> Connection | None:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                row = await fetch_one(
                    unit_of_work.connection,
                    f"SELECT {_CONNECTION_COLUMNS} FROM connections WHERE id = ?",
                    (connection_id,),
                )
                await unit_of_work.commit()
        except aiosqlite.Error as error:
            raise ConnectionRegistryPersistenceError(
                "Connection could not be loaded"
            ) from error
        if row is None:
            return None
        try:
            return _connection_from_row(row)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ConnectionRegistryPersistenceError(
                "Persisted Connection is invalid"
            ) from error

    async def update_connection(
        self,
        connection: Connection,
        *,
        expected_revision: int,
    ) -> None:
        if not isinstance(connection, Connection):
            raise TypeError("connection must be a Connection")
        if expected_revision < 0 or connection.revision != expected_revision + 1:
            raise ConnectionRegistryConflictError(
                "Connection revision does not follow the expected revision"
            )
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                cursor = await unit_of_work.connection.execute(
                    "UPDATE connections SET kind = ?, display_name = ?, "
                    "display_qualifier = ?, scope = ?, owner_user_id = ?, transport = ?, "
                    "auth_type = ?, state = ?, config_json = ?, secret_ref = ?, "
                    "connected_at = ?, last_checked_at = ?, last_error_code = ?, revision = ?, "
                    "created_at = ?, updated_at = ? WHERE id = ? AND revision = ?",
                    (*_connection_values(connection), connection.id, expected_revision),
                )
                try:
                    changed = cursor.rowcount
                finally:
                    await cursor.close()
                if changed != 1:
                    existing = await fetch_one(
                        unit_of_work.connection,
                        "SELECT id FROM connections WHERE id = ?",
                        (connection.id,),
                    )
                    if existing is None:
                        raise ConnectionRegistryNotFoundError(
                            "The Connection was not found"
                        )
                    raise ConnectionRegistryConflictError(
                        "The Connection revision changed"
                    )
                await unit_of_work.commit()
        except (ConnectionRegistryConflictError, ConnectionRegistryNotFoundError):
            raise
        except aiosqlite.IntegrityError as error:
            raise ConnectionRegistryConflictError(
                "Connection update violated a persistence constraint"
            ) from error
        except aiosqlite.Error as error:
            raise ConnectionRegistryPersistenceError(
                "Connection could not be updated"
            ) from error

    async def save_tool_snapshot(self, snapshot: ToolRegistrySnapshot) -> None:
        if not isinstance(snapshot, ToolRegistrySnapshot):
            raise TypeError("snapshot must be a ToolRegistrySnapshot")
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                connection = await fetch_one(
                    unit_of_work.connection,
                    "SELECT scope, owner_user_id FROM connections WHERE id = ?",
                    (snapshot.connection_id,),
                )
                if connection is None:
                    raise ConnectionRegistryNotFoundError(
                        "The Tool Connection was not found"
                    )
                if (
                    str(connection["scope"]) != snapshot.connection_scope.value
                    or _optional_text(connection, "owner_user_id")
                    != snapshot.connection_owner_user_id
                ):
                    raise ConnectionRegistryConflictError(
                        "The Tool Connection scope changed"
                    )
                existing = await fetch_one(
                    unit_of_work.connection,
                    f"SELECT {_TOOL_COLUMNS} FROM connection_tools ct "
                    "JOIN connections c ON c.id = ct.connection_id "
                    "WHERE ct.stable_tool_id = ?",
                    (snapshot.stable_tool_id,),
                )
                if existing is None:
                    await unit_of_work.connection.execute(
                        "INSERT INTO connection_tools "
                        "(stable_tool_id, connection_id, remote_name, permission, schema_json, "
                        "schema_fingerprint, state, discovered_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        _tool_values(snapshot),
                    )
                else:
                    persisted = _tool_snapshot_from_row(existing)
                    if persisted.connection_id != snapshot.connection_id:
                        raise ConnectionRegistryConflictError(
                            "The stable Tool ID belongs to another Connection"
                        )
                    if snapshot.discovered_at < persisted.discovered_at:
                        raise ConnectionRegistryConflictError(
                            "The Tool snapshot is older than the persisted snapshot"
                        )
                    if snapshot.discovered_at == persisted.discovered_at:
                        if snapshot != persisted:
                            raise ConnectionRegistryConflictError(
                                "The Tool snapshot conflicts at the same discovery time"
                            )
                    else:
                        await unit_of_work.connection.execute(
                            "UPDATE connection_tools SET remote_name = ?, permission = ?, "
                            "schema_json = ?, schema_fingerprint = ?, state = ?, discovered_at = ? "
                            "WHERE stable_tool_id = ? AND connection_id = ?",
                            (
                                snapshot.remote_name,
                                snapshot.permission.value,
                                snapshot.canonical_schema_json,
                                snapshot.schema_fingerprint,
                                snapshot.state.value,
                                snapshot.discovered_at.isoformat(),
                                snapshot.stable_tool_id,
                                snapshot.connection_id,
                            ),
                        )
                await unit_of_work.commit()
        except (ConnectionRegistryConflictError, ConnectionRegistryNotFoundError):
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ConnectionRegistryPersistenceError(
                "Persisted Tool Registry data is invalid"
            ) from error
        except aiosqlite.IntegrityError as error:
            raise ConnectionRegistryConflictError(
                "Tool snapshot violated a persistence constraint"
            ) from error
        except aiosqlite.Error as error:
            raise ConnectionRegistryPersistenceError(
                "Tool snapshot could not be persisted"
            ) from error

    async def get_tool_snapshot(self, stable_tool_id: str) -> ToolRegistrySnapshot | None:
        row = await self._tool_row(stable_tool_id)
        if row is None:
            return None
        try:
            return _tool_snapshot_from_row(row)
        except (KeyError, TypeError, ValueError) as error:
            raise ConnectionRegistryPersistenceError(
                "Persisted Tool Registry snapshot is invalid"
            ) from error

    async def resolve(self, tool_id: str) -> ResolvedTool | None:
        row = await self._tool_row(tool_id)
        if row is None:
            return None
        try:
            snapshot = _tool_snapshot_from_row(row)
            resolved = snapshot.as_resolved_tool()
            connection_active = str(row["connection_state"]) == ConnectionState.CONNECTED.value
            return replace(resolved, active=resolved.active and connection_active)
        except (KeyError, TypeError, ValueError) as error:
            raise ConnectionRegistryPersistenceError(
                "Persisted Tool resolution data is invalid"
            ) from error

    async def _tool_row(self, stable_tool_id: str) -> aiosqlite.Row | None:
        try:
            async with self._runtime(), self._database.create() as unit_of_work:
                row = await fetch_one(
                    unit_of_work.connection,
                    f"SELECT {_TOOL_COLUMNS} FROM connection_tools ct "
                    "JOIN connections c ON c.id = ct.connection_id "
                    "WHERE ct.stable_tool_id = ?",
                    (stable_tool_id,),
                )
                await unit_of_work.commit()
                return row
        except aiosqlite.Error as error:
            raise ConnectionRegistryPersistenceError(
                "Tool Registry snapshot could not be loaded"
            ) from error


def _tool_values(snapshot: ToolRegistrySnapshot) -> tuple[object, ...]:
    return (
        snapshot.stable_tool_id,
        snapshot.connection_id,
        snapshot.remote_name,
        snapshot.permission.value,
        snapshot.canonical_schema_json,
        snapshot.schema_fingerprint,
        snapshot.state.value,
        snapshot.discovered_at.isoformat(),
    )
