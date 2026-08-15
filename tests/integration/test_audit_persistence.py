"""Append-only Audit migration, persistence, retention, and atomicity tests."""

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.passwords import Argon2idPasswordHasher
from pangi.adapters.outbound.persistence.sqlite.audit import (
    SqliteAuditStore,
    SqliteAuditWriter,
)
from pangi.adapters.outbound.persistence.sqlite.auth import SqliteBootstrapStore
from pangi.adapters.outbound.persistence.sqlite.engine import SqliteMigrationAdmin
from pangi.adapters.outbound.persistence.sqlite.errors import MigrationApplyError
from pangi.adapters.outbound.persistence.sqlite.factory import (
    build_bootstrap_admin,
    build_sqlite_database,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.audit import AuditEventDraft, AuditStoreQuery
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.services.audit import core_audit_redaction_service
from pangi.application.services.bootstrap_admin import BootstrapAdminService
from pangi.domain.audit import AuditOutcome

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
    return paths, config


def _writer() -> SqliteAuditWriter:
    return SqliteAuditWriter(core_audit_redaction_service())


def _store_query(*, action: str | None = None) -> AuditStoreQuery:
    return AuditStoreQuery(
        actor_id=None,
        actions=() if action is None else (action,),
        resource_type=None,
        resource_id=None,
        outcomes=(),
        created_from=None,
        created_to=None,
        limit=100,
        after=None,
    )


def test_migration_and_bootstrap_actions_append_safe_audit_events(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    database = build_sqlite_database(paths, config)
    bootstrap = build_bootstrap_admin(database, config)

    grant = asyncio.run(bootstrap.issue_url())
    no_op = asyncio.run(bootstrap.issue_url())
    rotated = asyncio.run(bootstrap.issue_url(rotate=True))
    assert grant.bootstrap_url is not None
    assert no_op.bootstrap_url is None
    assert rotated.bootstrap_url is not None
    first_token = grant.bootstrap_url.partition("#")[2]
    token = rotated.bootstrap_url.partition("#")[2]
    asyncio.run(
        bootstrap.create_admin(
            token=token,
            local_id="owner",
            display_name="Owner",
            password="correct horse battery staple",
        )
    )

    with sqlite3.connect(paths.database_file) as connection:
        rows = connection.execute(
            "SELECT action, actor_id, resource_type, metadata_json "
            "FROM audit_events ORDER BY created_at, id"
        ).fetchall()
        audit_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(audit_events)"
        ).fetchall()
    assert {row[0] for row in rows} == {
        "storage.migrations_applied",
        "bootstrap.grant_issued",
        "bootstrap.grant_rotated",
        "bootstrap.admin_created",
    }
    assert len(rows) == 4
    assert audit_foreign_keys == []
    by_action = {row[0]: row for row in rows}
    assert by_action["storage.migrations_applied"][1:3] == (
        "system.migration",
        "database_schema",
    )
    assert by_action["bootstrap.admin_created"][1:3] == (
        "system.bootstrap",
        "user",
    )
    database_bytes = paths.database_file.read_bytes()
    assert first_token.encode() not in database_bytes
    assert token.encode() not in database_bytes
    assert b"correct horse battery staple" not in database_bytes


def test_audit_rows_are_immutable_until_retention_expires(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    database = build_sqlite_database(paths, config)
    store = SqliteAuditStore(database, _writer())
    current = asyncio.run(
        store.append_event(
            AuditEventDraft(
                actor_id="admin-user-000001",
                action="tool_policy.updated",
                resource_type="tool_policy",
                resource_id="policy-identifier-001",
                outcome=AuditOutcome.SUCCEEDED,
                created_at=NOW,
                after_summary={"state": "active"},
            )
        )
    )
    expired = asyncio.run(
        store.append_event(
            AuditEventDraft(
                actor_id="system.retention",
                action="audit.retention_seeded",
                resource_type="audit_event",
                resource_id="expired-audit-record-01",
                outcome=AuditOutcome.SUCCEEDED,
                created_at=datetime(2000, 1, 1, tzinfo=UTC),
            )
        )
    )

    with sqlite3.connect(paths.database_file) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE audit_events SET actor_id = ? WHERE id = ?",
                ("other-actor", current.id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="retention"):
            connection.execute("DELETE FROM audit_events WHERE id = ?", (current.id,))

    removed = asyncio.run(
        store.purge_expired(
            before=datetime(2001, 1, 1, tzinfo=UTC),
            limit=10,
        )
    )
    assert removed == 1
    remaining = asyncio.run(store.list_events(_store_query()))
    assert current.id in {event.id for event in remaining}
    assert expired.id not in {event.id for event in remaining}
    filtered = asyncio.run(
        store.list_events(_store_query(action="tool_policy.updated"))
    )
    assert [event.id for event in filtered] == [current.id]


class FailingAuditWriter(SqliteAuditWriter):
    async def insert(self, *_args: object, **_kwargs: object):  # type: ignore[no-untyped-def]
        raise RuntimeError("safe audit failure")


def test_migration_batch_rolls_back_when_audit_write_fails(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    admin = SqliteMigrationAdmin(
        paths,
        config.storage,
        audit_writer=FailingAuditWriter(core_audit_redaction_service()),
    )

    with pytest.raises(MigrationApplyError, match="rolled back"):
        asyncio.run(admin.apply())

    with sqlite3.connect(paths.database_file) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        user_version = connection.execute("PRAGMA user_version").fetchone()
    assert tables == []
    assert user_version == (0,)


def test_bootstrap_state_rolls_back_when_audit_write_fails(tmp_path: Path) -> None:
    paths, config = _initialized_runtime(tmp_path)
    database = build_sqlite_database(paths, config)
    asyncio.run(database.start())
    asyncio.run(database.close())
    service = BootstrapAdminService(
        SqliteBootstrapStore(
            database,
            FailingAuditWriter(core_audit_redaction_service()),
        ),
        Argon2idPasswordHasher(),
        public_base_url="http://127.0.0.1:8787",
        grant_ttl_minutes=30,
        clock=lambda: NOW,
        secret_factory=lambda: "bootstrap-token-value-0000000000001",
    )

    with pytest.raises(RuntimeError, match="safe audit failure"):
        asyncio.run(service.issue_url())

    with sqlite3.connect(paths.database_file) as connection:
        assert connection.execute("SELECT count(*) FROM bootstrap_grants").fetchone() == (0,)
        actions = connection.execute(
            "SELECT action FROM audit_events ORDER BY created_at, id"
        ).fetchall()
    assert actions == [("storage.migrations_applied",)]
