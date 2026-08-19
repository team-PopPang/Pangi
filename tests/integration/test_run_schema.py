"""Run Core SQLite schema constraints."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.persistence.sqlite.engine import SqliteMigrationAdmin
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.paths import RuntimePaths

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _initialized_runtime(tmp_path: Path) -> tuple[RuntimePaths, PangiConfig]:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    asyncio.run(SqliteMigrationAdmin(paths, config.storage).apply())
    return paths, config


def _insert_user(connection: sqlite3.Connection, user_id: str, role: str = "member") -> None:
    timestamp = NOW.isoformat()
    connection.execute(
        "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'active', ?, ?)",
        (user_id, user_id, role, timestamp, timestamp),
    )


def _insert_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    request_id: str,
    principal_id: str,
    idempotency_key: str,
    state: str = "queued",
    finished_at: str | None = None,
    attachments_json: str = "[]",
) -> None:
    timestamp = NOW.isoformat()
    connection.execute(
        "INSERT INTO runs "
        "(id, request_id, principal_id, trigger, state, mode, request_text, "
        "attachments_json, idempotency_key, created_at, updated_at, queued_at, finished_at) "
        "VALUES (?, ?, ?, 'dashboard', ?, NULL, 'safe normalized request', ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            request_id,
            principal_id,
            state,
            attachments_json,
            idempotency_key,
            timestamp,
            timestamp,
            timestamp,
            finished_at,
        ),
    )


def test_run_schema_enforces_identity_state_json_and_non_global_idempotency(
    tmp_path: Path,
) -> None:
    paths, _config = _initialized_runtime(tmp_path)
    timestamp = NOW.isoformat()
    with sqlite3.connect(paths.database_file) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _insert_user(connection, "member-user-00001")
        _insert_run(
            connection,
            run_id="run-identifier-0001",
            request_id="request-identifier-1",
            principal_id="member-user-00001",
            idempotency_key="shared-key",
        )
        _insert_run(
            connection,
            run_id="run-identifier-0002",
            request_id="request-identifier-2",
            principal_id="member-user-00001",
            idempotency_key="shared-key",
        )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(
                connection,
                run_id="run-identifier-0003",
                request_id="request-identifier-1",
                principal_id="member-user-00001",
                idempotency_key="different-key",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(
                connection,
                run_id="run-identifier-0004",
                request_id="request-identifier-4",
                principal_id="missing-user-0001",
                idempotency_key="missing-owner",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(
                connection,
                run_id="run-identifier-0005",
                request_id="request-identifier-5",
                principal_id="member-user-00001",
                idempotency_key="invalid-state",
                state="unknown",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(
                connection,
                run_id="run-identifier-0006",
                request_id="request-identifier-6",
                principal_id="member-user-00001",
                idempotency_key="running-without-lease",
                state="running",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(
                connection,
                run_id="run-identifier-0007",
                request_id="request-identifier-7",
                principal_id="member-user-00001",
                idempotency_key="terminal-without-time",
                state="failed",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(
                connection,
                run_id="run-identifier-0008",
                request_id="request-identifier-8",
                principal_id="member-user-00001",
                idempotency_key="invalid-json",
                attachments_json="{}",
            )

        _insert_run(
            connection,
            run_id="run-identifier-0009",
            request_id="request-identifier-9",
            principal_id="member-user-00001",
            idempotency_key="terminal-valid",
            state="completed",
            finished_at=timestamp,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM runs WHERE idempotency_key = 'shared-key'"
        ).fetchone() == (2,)


def test_step_and_event_schema_enforces_attempt_order_and_same_run_reference(
    tmp_path: Path,
) -> None:
    paths, _config = _initialized_runtime(tmp_path)
    timestamp = NOW.isoformat()
    with sqlite3.connect(paths.database_file) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _insert_user(connection, "member-user-00001")
        for index in (1, 2):
            _insert_run(
                connection,
                run_id=f"run-identifier-000{index}",
                request_id=f"request-identifier-{index}",
                principal_id="member-user-00001",
                idempotency_key=f"request-once-{index}",
            )
        connection.execute(
            "INSERT INTO run_steps "
            "(id, run_id, node_id, type, state, requirement, idempotent, attempt, "
            "created_at, updated_at) "
            "VALUES ('step-identifier-001', 'run-identifier-0001', 'collect', 'subagent', "
            "'queued', 'required', 1, 1, ?, ?)",
            (timestamp, timestamp),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO run_steps "
                "(id, run_id, node_id, type, state, requirement, idempotent, attempt, "
                "created_at, updated_at) "
                "VALUES ('step-identifier-002', 'run-identifier-0001', 'collect', "
                "'subagent', 'queued', 'required', 1, 1, ?, ?)",
                (timestamp, timestamp),
            )
        connection.execute(
            "INSERT INTO run_events "
            "(run_id, event_index, type, visibility, step_id, attributes_json, created_at) "
            "VALUES ('run-identifier-0001', 1, 'step.queued', 'public', "
            "'step-identifier-001', '{}', ?)",
            (timestamp,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO run_events "
                "(run_id, event_index, type, visibility, attributes_json, created_at) "
                "VALUES ('run-identifier-0001', 1, 'run.received', 'public', '{}', ?)",
                (timestamp,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO run_events "
                "(run_id, event_index, type, visibility, step_id, attributes_json, created_at) "
                "VALUES ('run-identifier-0002', 1, 'step.queued', 'public', "
                "'step-identifier-001', '{}', ?)",
                (timestamp,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO run_events "
                "(run_id, event_index, type, visibility, attributes_json, created_at) "
                "VALUES ('run-identifier-0001', 2, 'run.received', 'owner', '{}', ?)",
                (timestamp,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO run_events "
                "(run_id, event_index, type, visibility, attributes_json, created_at) "
                "VALUES ('run-identifier-0001', 2, 'run.received', 'public', '[]', ?)",
                (timestamp,),
            )


def test_orchestration_plan_and_step_payloads_are_bounded_and_immutable(
    tmp_path: Path,
) -> None:
    paths, _config = _initialized_runtime(tmp_path)
    timestamp = NOW.isoformat()
    with sqlite3.connect(paths.database_file) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _insert_user(connection, "member-user-00001")
        _insert_run(
            connection,
            run_id="run-identifier-0001",
            request_id="request-identifier-1",
            principal_id="member-user-00001",
            idempotency_key="execution-plan",
        )
        connection.execute(
            "INSERT INTO run_execution_plans "
            "(run_id, mode, schema_version, plan_json, plan_fingerprint, created_at) "
            "VALUES (?, 'direct', 'orchestration-execution-v1', ?, ?, ?)",
            (
                "run-identifier-0001",
                '{"mode":"direct"}',
                "a" * 64,
                timestamp,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE run_execution_plans SET plan_json = ? WHERE run_id = ?",
                ('{"mode":"delegate"}', "run-identifier-0001"),
            )

        connection.execute(
            "INSERT INTO run_steps "
            "(id, run_id, node_id, type, state, requirement, idempotent, attempt, "
            "depends_on_json, task_json, created_at, updated_at) "
            "VALUES ('step-identifier-001', 'run-identifier-0001', 'collect', "
            "'subagent', 'queued', 'required', 0, 1, '[]', '{}', ?, ?)",
            (timestamp, timestamp),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE run_steps SET task_json = ? WHERE id = ?",
                ('{"changed":true}', "step-identifier-001"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE run_steps SET result_json = '{}' WHERE id = ?",
                ("step-identifier-001",),
            )


def test_idempotency_schema_scopes_keys_by_principal_and_route(tmp_path: Path) -> None:
    paths, _config = _initialized_runtime(tmp_path)
    created_at = NOW.isoformat()
    expires_at = (NOW + timedelta(days=1)).isoformat()
    with sqlite3.connect(paths.database_file) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _insert_user(connection, "member-user-00001")
        _insert_user(connection, "member-user-00002")

        def insert_record(
            principal_id: str,
            route_key: str,
            key: str,
            *,
            fingerprint: str = "a" * 64,
            state: str = "completed",
            response_json: str | None = '{"run_id":"run-identifier-0001"}',
            expires: str = expires_at,
        ) -> None:
            connection.execute(
                "INSERT INTO api_idempotency_records "
                "(principal_id, route_key, idempotency_key, request_fingerprint, "
                "response_json, state, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    principal_id,
                    route_key,
                    key,
                    fingerprint,
                    response_json,
                    state,
                    expires,
                    created_at,
                    created_at,
                ),
            )

        insert_record("member-user-00001", "runs.create", "same-key")
        insert_record("member-user-00001", "runs.cancel", "same-key")
        insert_record("member-user-00002", "runs.create", "same-key")
        with pytest.raises(sqlite3.IntegrityError):
            insert_record("member-user-00001", "runs.create", "same-key")
        with pytest.raises(sqlite3.IntegrityError):
            insert_record(
                "member-user-00001",
                "runs.create",
                "bad-fingerprint",
                fingerprint="not-a-digest",
            )
        with pytest.raises(sqlite3.IntegrityError):
            insert_record(
                "member-user-00001",
                "runs.create",
                "completed-without-response",
                response_json=None,
            )
        with pytest.raises(sqlite3.IntegrityError):
            insert_record(
                "member-user-00001",
                "runs.create",
                "bad-expiry",
                expires=created_at,
            )

        assert connection.execute(
            "SELECT COUNT(*) FROM api_idempotency_records WHERE idempotency_key = 'same-key'"
        ).fetchone() == (3,)
