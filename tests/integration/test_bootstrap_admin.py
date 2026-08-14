"""Bootstrap Admin persistence, rotation, and transaction tests."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.passwords import Argon2idPasswordHasher
from pangi.adapters.outbound.persistence.sqlite.auth import SqliteBootstrapStore
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.bootstrap import BootstrapIssueStatus
from pangi.application.ports.bootstrap_admin import (
    BootstrapIdentityConflictError,
    InvalidBootstrapGrantError,
)
from pangi.application.services.bootstrap_admin import BootstrapAdminService


def _service(
    tmp_path: Path,
    *,
    now: datetime,
    secrets: list[str],
) -> tuple[BootstrapAdminService, Path]:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    database = SqliteDatabase(paths, config.storage)
    service = BootstrapAdminService(
        SqliteBootstrapStore(database),
        Argon2idPasswordHasher(),
        public_base_url="http://127.0.0.1:8787",
        grant_ttl_minutes=30,
        clock=lambda: now,
        secret_factory=lambda: secrets.pop(0),
    )
    return service, paths.database_file


def _fragment(result_url: str | None) -> str:
    assert result_url is not None
    token = urlsplit(result_url).fragment
    assert token
    return token


def test_grant_is_issued_once_and_only_its_hash_is_persisted(tmp_path: Path) -> None:
    raw_token = "first-bootstrap-token-value-00000001"
    service, database_file = _service(
        tmp_path,
        now=datetime(2026, 8, 15, 1, 0, tzinfo=UTC),
        secrets=[raw_token, "unused-bootstrap-token-value-000002"],
    )

    first = asyncio.run(service.issue_url())
    second = asyncio.run(service.issue_url())

    assert first.status is BootstrapIssueStatus.ISSUED
    assert first.bootstrap_url == f"http://127.0.0.1:8787/bootstrap#{raw_token}"
    assert second.status is BootstrapIssueStatus.ALREADY_ISSUED
    assert second.bootstrap_url is None
    with sqlite3.connect(database_file) as connection:
        row = connection.execute(
            "SELECT token_hash, expires_at FROM bootstrap_grants"
        ).fetchone()
        assert row is not None
        assert len(row[0]) == 64
        assert row[0] != raw_token
    assert raw_token.encode() not in database_file.read_bytes()


def test_rotation_invalidates_old_grant_and_admin_consumption_is_atomic(
    tmp_path: Path,
) -> None:
    old_token = "old-bootstrap-token-value-000000001"
    new_token = "new-bootstrap-token-value-000000001"
    service, database_file = _service(
        tmp_path,
        now=datetime(2026, 8, 15, 2, 0, tzinfo=UTC),
        secrets=[old_token, new_token, "unused-after-admin-token-0000000001"],
    )
    old = asyncio.run(service.issue_url())
    rotated = asyncio.run(service.issue_url(rotate=True))

    with pytest.raises(InvalidBootstrapGrantError, match="invalid or unavailable"):
        asyncio.run(
            service.create_admin(
                token=_fragment(old.bootstrap_url),
                local_id="owner@example.com",
                display_name="Owner",
                password="correct horse battery staple",
            )
        )

    created = asyncio.run(
        service.create_admin(
            token=_fragment(rotated.bootstrap_url),
            local_id="Owner@Example.com",
            display_name=" Pangi Owner ",
            password="correct horse battery staple",
        )
    )

    assert created.local_id == "owner@example.com"
    assert created.role == "admin"
    with sqlite3.connect(database_file) as connection:
        user = connection.execute(
            "SELECT role, status, display_name FROM users"
        ).fetchone()
        identity = connection.execute(
            "SELECT provider, subject, password_hash FROM auth_identities"
        ).fetchone()
        grants = connection.execute(
            "SELECT revoked_at, consumed_at, consumed_by_user_id "
            "FROM bootstrap_grants ORDER BY created_at, id"
        ).fetchall()
    assert user == ("admin", "active", "Pangi Owner")
    assert identity is not None
    assert identity[:2] == ("local", "owner@example.com")
    assert identity[2].startswith("$argon2id$")
    assert sum(row[0] is not None for row in grants) == 1
    assert sum(row[1] is not None for row in grants) == 1
    assert next(row[2] for row in grants if row[1] is not None) == created.user_id
    database_bytes = database_file.read_bytes()
    assert new_token.encode() not in database_bytes
    assert b"correct horse battery staple" not in database_bytes

    with pytest.raises(InvalidBootstrapGrantError, match="invalid or unavailable"):
        asyncio.run(
            service.create_admin(
                token=new_token,
                local_id="second-owner",
                display_name="Second Owner",
                password="another secure password",
            )
        )
    assert asyncio.run(service.issue_url(rotate=True)).status is BootstrapIssueStatus.ADMIN_EXISTS


def test_expired_grant_is_rejected_without_revealing_its_state(tmp_path: Path) -> None:
    issued_at = datetime(2026, 8, 15, 3, 0, tzinfo=UTC)
    current = [issued_at]
    token = "expired-bootstrap-token-value-000001"
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    service = BootstrapAdminService(
        SqliteBootstrapStore(SqliteDatabase(paths, config.storage)),
        Argon2idPasswordHasher(),
        public_base_url="http://127.0.0.1:8787",
        grant_ttl_minutes=30,
        clock=lambda: current[0],
        secret_factory=lambda: token,
    )
    asyncio.run(service.issue_url())
    current[0] = issued_at + timedelta(minutes=31)

    with pytest.raises(InvalidBootstrapGrantError) as captured:
        asyncio.run(
            service.create_admin(
                token=token,
                local_id="expired-owner",
                display_name="Expired Owner",
                password="correct horse battery staple",
            )
        )

    assert str(captured.value) == "Bootstrap Grant is invalid or unavailable"


def test_identity_conflict_rolls_back_user_and_grant_consumption(tmp_path: Path) -> None:
    token = "conflict-bootstrap-token-value-00001"
    now = datetime(2026, 8, 15, 4, 0, tzinfo=UTC)
    service, database_file = _service(tmp_path, now=now, secrets=[token])
    asyncio.run(service.issue_url())
    with sqlite3.connect(database_file) as connection:
        timestamp = now.isoformat()
        connection.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
            ("existing-user-0001", "Member", "member", "active", timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO auth_identities VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "existing-identity-0001",
                "existing-user-0001",
                "local",
                "duplicate-owner",
                Argon2idPasswordHasher().hash("existing secure password"),
                timestamp,
                timestamp,
            ),
        )

    with pytest.raises(BootstrapIdentityConflictError):
        asyncio.run(
            service.create_admin(
                token=token,
                local_id="duplicate-owner",
                display_name="Duplicate",
                password="correct horse battery staple",
            )
        )

    with sqlite3.connect(database_file) as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone() == (1,)
        grant = connection.execute(
            "SELECT consumed_at, consumed_by_user_id FROM bootstrap_grants"
        ).fetchone()
    assert grant == (None, None)
