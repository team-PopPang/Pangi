"""Persistent Local Login and Session state transition tests."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.login_attempts import InMemoryLoginAttemptLimiter
from pangi.adapters.outbound.passwords import Argon2idPasswordHasher
from pangi.adapters.outbound.persistence.sqlite.auth import SqliteBootstrapStore
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.sessions import SqliteAuthSessionStore
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.ports.auth import AuthenticationRequiredError
from pangi.application.services.auth import AuthSessionService
from pangi.application.services.bootstrap_admin import BootstrapAdminService


def test_expired_or_disabled_persistent_session_is_rejected(tmp_path: Path) -> None:
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
    hasher = Argon2idPasswordHasher()
    now = [datetime(2030, 1, 1, tzinfo=UTC)]
    bootstrap = BootstrapAdminService(
        SqliteBootstrapStore(database),
        hasher,
        public_base_url="http://127.0.0.1:8787",
        grant_ttl_minutes=30,
        clock=lambda: now[0],
        secret_factory=lambda: "bootstrap-token-value-00000000000000001",
    )
    issued_grant = asyncio.run(bootstrap.issue_url())
    assert issued_grant.bootstrap_url is not None
    token = issued_grant.bootstrap_url.partition("#")[2]
    asyncio.run(
        bootstrap.create_admin(
            token=token,
            local_id="owner",
            display_name="Owner",
            password="correct horse battery staple",
        )
    )
    secrets = iter(("s" * 43, "c" * 43, "n" * 43, "r" * 43))
    sessions = AuthSessionService(
        SqliteAuthSessionStore(database),
        hasher,
        InMemoryLoginAttemptLimiter(attempt_limit=5, window_seconds=300),
        dummy_password_hash=hasher.hash("pangi-invalid-login-placeholder"),
        session_ttl_minutes=5,
        rotation_minutes=5,
        clock=lambda: now[0],
        secret_factory=lambda: next(secrets),
    )

    first = asyncio.run(
        sessions.login(
            local_id="owner",
            password="correct horse battery staple",
            source="127.0.0.1",
        )
    )
    assert asyncio.run(
        sessions.current_session(session_token=first.session_token)
    ).principal.display_name == "Owner"

    now[0] += timedelta(minutes=6)
    with pytest.raises(AuthenticationRequiredError):
        asyncio.run(sessions.current_session(session_token=first.session_token))
    with sqlite3.connect(paths.database_file) as connection:
        state = connection.execute("SELECT state FROM auth_sessions").fetchone()
    assert state == ("expired",)

    second = asyncio.run(
        sessions.login(
            local_id="owner",
            password="correct horse battery staple",
            source="127.0.0.1",
        )
    )
    with sqlite3.connect(paths.database_file) as connection:
        connection.execute("UPDATE users SET status = 'disabled'")
        connection.commit()
    with pytest.raises(AuthenticationRequiredError):
        asyncio.run(sessions.current_session(session_token=second.session_token))
