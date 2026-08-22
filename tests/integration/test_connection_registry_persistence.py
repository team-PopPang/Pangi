"""Connection and Stable Tool Registry SQLite integration tests."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.factory import build_connection_registry
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.connections import ToolRegistrySnapshot
from pangi.application.contracts.tool_guardrails import (
    ApprovalGrant,
    GuardedToolCall,
    ResolvedTool,
    ToolBudgetReservation,
    ToolCallRequest,
    ToolGuardrailBlockedError,
    ToolPolicy,
)
from pangi.application.ports.connections import (
    ConnectionRegistryConflictError,
    ConnectionRegistryNotFoundError,
)
from pangi.application.services.connections import ToolRegistrySnapshotFactory
from pangi.application.services.tool_guardrails import (
    GuardedToolExecutionService,
    ToolGuardrailService,
)
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.connections import (
    Connection,
    ConnectionAuthType,
    ConnectionScope,
    ConnectionState,
    ConnectionToolState,
    ConnectionTransport,
    transition_connection,
)
from pangi.domain.tool_guardrails import ToolGuardrailErrorCode, ToolPermission

NOW = datetime(2030, 1, 1, tzinfo=UTC)
USER_ID = "member-user-00001"


def _database(tmp_path: Path) -> SqliteDatabase:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    return SqliteDatabase(paths, config.storage)


async def _insert_user(database: SqliteDatabase) -> None:
    async with database.create() as unit_of_work:
        timestamp = NOW.isoformat()
        await unit_of_work.connection.execute(
            "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
            "VALUES (?, 'Registry Test', 'member', 'active', ?, ?)",
            (USER_ID, timestamp, timestamp),
        )
        await unit_of_work.commit()


def _user_connection(
    *,
    state: ConnectionState = ConnectionState.CONNECTED,
) -> Connection:
    connected_at = NOW if state is ConnectionState.CONNECTED else None
    return Connection(
        id="connection-user-0001",
        kind="linear",
        display_name="Linear",
        display_qualifier="Engineering",
        scope=ConnectionScope.USER,
        owner_user_id=USER_ID,
        transport=ConnectionTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.example.test",
        auth_type=ConnectionAuthType.OAUTH,
        secret_ref="secret://connection-user-0001/oauth",
        state=state,
        created_at=NOW,
        updated_at=NOW,
        connected_at=connected_at,
        last_checked_at=connected_at,
    )


def _instance_connection() -> Connection:
    return Connection(
        id="connection-instance-0001",
        kind="filesystem",
        display_name="Filesystem",
        scope=ConnectionScope.INSTANCE,
        transport=ConnectionTransport.STDIO,
        command="/opt/pangi/bin/filesystem-mcp",
        args=("--readonly", "/srv/shared"),
        auth_type=ConnectionAuthType.ENVIRONMENT,
        secret_ref="secret://connection-instance-0001/environment",
        state=ConnectionState.DISCONNECTED,
        created_at=NOW,
        updated_at=NOW,
    )


def _snapshot(
    connection: Connection,
    *,
    state: ConnectionToolState = ConnectionToolState.ACTIVE,
    discovered_at: datetime = NOW,
    stable_tool_id: str = "linear.issue.create",
) -> ToolRegistrySnapshot:
    return ToolRegistrySnapshotFactory().build(
        connection=connection,
        stable_tool_id=stable_tool_id,
        remote_name="create_issue",
        permission=ToolPermission.WRITE,
        input_schema={"properties": {"title": {"type": "string"}}, "type": "object"},
        state=state,
        discovered_at=discovered_at,
    )


def test_connections_round_trip_without_secret_body_columns(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            registry = build_connection_registry(database)
            user_connection = _user_connection()
            instance_connection = _instance_connection()

            await registry.add_connection(user_connection)
            await registry.add_connection(instance_connection)

            assert await registry.get_connection(user_connection.id) == user_connection
            assert await registry.get_connection(instance_connection.id) == instance_connection
            async with database.create() as unit_of_work:
                cursor = await unit_of_work.connection.execute("PRAGMA table_info(connections)")
                columns = {str(row[1]) for row in await cursor.fetchall()}
                await cursor.close()
                cursor = await unit_of_work.connection.execute(
                    "SELECT config_json, secret_ref FROM connections WHERE id = ?",
                    (user_connection.id,),
                )
                row = await cursor.fetchone()
                await cursor.close()
                await unit_of_work.commit()
            assert row is not None
            assert columns >= {"config_json", "secret_ref"}
            assert not ({"secret", "token", "access_token", "refresh_token"} & columns)
            assert json.loads(str(row[0])) == {
                "args": [],
                "command": None,
                "endpoint": "https://mcp.example.test",
                "schema_version": 1,
            }
            assert str(row[1]) == user_connection.secret_ref
            assert "secret://" not in str(row[0])
        finally:
            await database.close()

    asyncio.run(scenario())


def test_connection_updates_use_revision_compare_and_swap(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            registry = build_connection_registry(database)
            disconnected = _user_connection(state=ConnectionState.DISCONNECTED)
            await registry.add_connection(disconnected)

            connecting = transition_connection(
                disconnected,
                ConnectionState.CONNECTING,
                at=NOW + timedelta(seconds=1),
            )
            await registry.update_connection(connecting, expected_revision=0)
            connected = transition_connection(
                connecting,
                ConnectionState.CONNECTED,
                at=NOW + timedelta(seconds=2),
            )
            await registry.update_connection(connected, expected_revision=1)
            assert await registry.get_connection(connected.id) == connected

            stale = replace(connected, display_name="Stale writer")
            with pytest.raises(ConnectionRegistryConflictError):
                await registry.update_connection(stale, expected_revision=1)

            missing = replace(connecting, id="connection-user-missing")
            with pytest.raises(ConnectionRegistryNotFoundError):
                await registry.update_connection(missing, expected_revision=0)
        finally:
            await database.close()

    asyncio.run(scenario())


def test_tool_snapshots_are_monotonic_and_stable_ids_are_global(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            registry = build_connection_registry(database)
            user_connection = _user_connection()
            instance_connection = _instance_connection()
            await registry.add_connection(user_connection)
            await registry.add_connection(instance_connection)

            active = _snapshot(user_connection)
            await registry.save_tool_snapshot(active)
            await registry.save_tool_snapshot(active)
            assert await registry.get_tool_snapshot(active.stable_tool_id) == active
            resolved = await registry.resolve(active.stable_tool_id)
            assert resolved is not None
            assert resolved.active
            assert resolved.connection_owner_user_id == USER_ID

            unavailable = _snapshot(
                user_connection,
                state=ConnectionToolState.UNAVAILABLE,
                discovered_at=NOW + timedelta(seconds=2),
            )
            await registry.save_tool_snapshot(unavailable)
            assert await registry.get_tool_snapshot(active.stable_tool_id) == unavailable
            resolved = await registry.resolve(active.stable_tool_id)
            assert resolved is not None
            assert not resolved.active

            with pytest.raises(ConnectionRegistryConflictError):
                await registry.save_tool_snapshot(active)
            with pytest.raises(ConnectionRegistryConflictError):
                await registry.save_tool_snapshot(
                    replace(unavailable, remote_name="conflicting_name")
                )
            with pytest.raises(ConnectionRegistryConflictError):
                await registry.save_tool_snapshot(
                    _snapshot(
                        instance_connection,
                        discovered_at=NOW + timedelta(seconds=3),
                    )
                )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resolver_requires_both_connected_connection_and_active_tool(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            registry = build_connection_registry(database)
            connection = _user_connection()
            await registry.add_connection(connection)
            snapshot = _snapshot(connection)
            await registry.save_tool_snapshot(snapshot)
            resolved = await registry.resolve(snapshot.stable_tool_id)
            assert resolved is not None and resolved.active

            disconnected = transition_connection(
                connection,
                ConnectionState.DISCONNECTED,
                at=NOW + timedelta(seconds=1),
            )
            await registry.update_connection(disconnected, expected_revision=0)
            resolved = await registry.resolve(snapshot.stable_tool_id)
            assert resolved is not None and not resolved.active
            assert await registry.resolve("unknown.tool") is None
        finally:
            await database.close()

    asyncio.run(scenario())


class MissingPolicyProvider:
    async def get_policy(self, *, tool_id: str, connection_id: str) -> ToolPolicy | None:
        return None


class NeverArgumentValidator:
    async def validate_arguments(
        self,
        *,
        tool: ResolvedTool,
        canonical_arguments_json: str,
    ) -> bool:
        raise AssertionError("a Tool without Policy cannot reach argument validation")


class NeverApprovalVerifier:
    async def resolve_approval(self, approval_reference: str) -> ApprovalGrant | None:
        raise AssertionError("a Tool without Policy cannot reach approval")


class NeverBudgetLedger:
    async def reserve_call(
        self,
        *,
        run_id: str,
        tool_id: str,
        policy_fingerprint: str,
        max_calls_per_run: int,
    ) -> ToolBudgetReservation:
        raise AssertionError("a Tool without Policy cannot reserve budget")


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[GuardedToolCall] = []

    async def execute(self, call: GuardedToolCall) -> object:
        self.calls.append(call)
        return "unexpected"


def test_sqlite_resolver_still_defaults_to_deny_without_policy(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            registry = build_connection_registry(database)
            connection = _user_connection()
            snapshot = _snapshot(connection)
            await registry.add_connection(connection)
            await registry.save_tool_snapshot(snapshot)
            executor = RecordingExecutor()
            service = GuardedToolExecutionService(
                ToolGuardrailService(
                    resolver=registry,
                    policy_provider=MissingPolicyProvider(),
                    argument_validator=NeverArgumentValidator(),
                    approval_verifier=NeverApprovalVerifier(),
                    budget_ledger=NeverBudgetLedger(),
                    clock=lambda: NOW,
                ),
                executor=executor,
            )
            actor = AuthenticatedPrincipal(
                USER_ID,
                "Actor",
                UserRole.MEMBER,
                UserStatus.ACTIVE,
            )

            with pytest.raises(ToolGuardrailBlockedError) as captured:
                await service.execute(
                    actor=actor,
                    request=ToolCallRequest(
                        run_id="run-identifier-0001",
                        principal_user_id=USER_ID,
                        tool_id=snapshot.stable_tool_id,
                        arguments={"title": "새 이슈"},
                    ),
                )
            assert captured.value.code is ToolGuardrailErrorCode.POLICY_MISSING
            assert executor.calls == []
        finally:
            await database.close()

    asyncio.run(scenario())


def test_migration_rejects_invalid_connection_and_tool_rows(tmp_path: Path) -> None:
    database = _database(tmp_path)

    async def migrate() -> None:
        await database.start()
        await database.close()

    asyncio.run(migrate())
    timestamp = NOW.isoformat()
    valid_config = json.dumps(
        {"args": [], "command": "/opt/mcp", "endpoint": None, "schema_version": 1},
        separators=(",", ":"),
        sort_keys=True,
    )
    connection_insert = (
        "INSERT INTO connections "
        "(id, kind, display_name, scope, owner_user_id, transport, auth_type, state, "
        "config_json, secret_ref, connected_at, last_checked_at, last_error_code, revision, "
        "created_at, updated_at) VALUES (?, 'fixture', 'Fixture', ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, 0, ?, ?)"
    )

    with sqlite3.connect(database.paths.database_file) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            connection_insert,
            (
                "connection-valid",
                "instance",
                None,
                "stdio",
                "none",
                "disconnected",
                valid_config,
                None,
                None,
                None,
                None,
                timestamp,
                timestamp,
            ),
        )
        invalid_connections = (
            (
                "connection-owner-missing",
                "user",
                None,
                "stdio",
                "none",
                "disconnected",
                valid_config,
                None,
                None,
                None,
                None,
                timestamp,
                timestamp,
            ),
            (
                "connection-auth-invalid",
                "instance",
                None,
                "stdio",
                "oauth",
                "disconnected",
                valid_config,
                "secret://oauth",
                None,
                None,
                None,
                timestamp,
                timestamp,
            ),
            (
                "connection-state-invalid",
                "instance",
                None,
                "stdio",
                "none",
                "connected",
                valid_config,
                None,
                None,
                None,
                None,
                timestamp,
                timestamp,
            ),
            (
                "connection-config-invalid",
                "instance",
                None,
                "stdio",
                "none",
                "disconnected",
                valid_config[:-1] + ',"token":"plaintext"}',
                None,
                None,
                None,
                None,
                timestamp,
                timestamp,
            ),
        )
        for values in invalid_connections:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(connection_insert, values)

        tool_insert = (
            "INSERT INTO connection_tools "
            "(stable_tool_id, connection_id, remote_name, permission, schema_json, "
            "schema_fingerprint, state, discovered_at) VALUES (?, 'connection-valid', "
            "'remote', ?, ?, ?, 'new', ?)"
        )
        connection.execute(
            tool_insert,
            ("tool-valid", "read", "{}", "0" * 64, timestamp),
        )
        invalid_tools = (
            ("tool-permission-invalid", "admin", "{}", "0" * 64, timestamp),
            ("tool-schema-invalid", "read", "[]", "0" * 64, timestamp),
            ("tool-fingerprint-invalid", "read", "{}", "G" * 64, timestamp),
            ("tool-time-invalid", "read", "{}", "0" * 64, "not-a-time"),
        )
        for tool_values in invalid_tools:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(tool_insert, tool_values)
