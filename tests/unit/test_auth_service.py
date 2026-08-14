"""Local authentication application-service tests."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from pangi.adapters.outbound.login_attempts import InMemoryLoginAttemptLimiter
from pangi.application.contracts.auth import (
    AuthenticatedPrincipal,
    LocalIdentityRecord,
    PasswordHashUpdate,
    StoredAuthSession,
)
from pangi.application.ports.auth import (
    AuthenticationRequiredError,
    CsrfRejectedError,
    InvalidCredentialsError,
    LoginRateLimitedError,
)
from pangi.application.services.auth import AuthSessionService, hash_bearer
from pangi.domain.auth import AuthSession, UserRole, UserStatus


class MemoryAuthStore:
    def __init__(self, identity: LocalIdentityRecord | None) -> None:
        self.identity = identity
        self.sessions: dict[str, StoredAuthSession] = {}
        self.created: AuthSession | None = None
        self.password_update: PasswordHashUpdate | None = None

    async def find_local_identity(self, *, subject: str) -> LocalIdentityRecord | None:
        if subject == "owner":
            return self.identity
        return None

    async def create_session(
        self,
        session: AuthSession,
        *,
        password_update: PasswordHashUpdate | None,
    ) -> None:
        assert self.identity is not None
        self.created = session
        self.password_update = password_update
        self.sessions[session.token_hash] = StoredAuthSession(
            session.id,
            session.csrf_hash,
            session.expires_at,
            session.created_at,
            None,
            self.identity.principal,
        )

    async def get_session(
        self,
        *,
        token_hash: str,
        at: datetime,
    ) -> StoredAuthSession | None:
        stored = self.sessions.get(token_hash)
        if stored is None or stored.expires_at <= at:
            return None
        return stored

    async def rotate_session(
        self,
        *,
        session_id: str,
        previous_token_hash: str,
        token_hash: str,
        csrf_hash: str,
        rotated_at: datetime,
    ) -> bool:
        stored = self.sessions.pop(previous_token_hash, None)
        if stored is None or stored.session_id != session_id:
            return False
        self.sessions[token_hash] = StoredAuthSession(
            stored.session_id,
            csrf_hash,
            stored.expires_at,
            stored.created_at,
            rotated_at,
            stored.principal,
        )
        return True

    async def revoke_session(
        self,
        *,
        session_id: str,
        token_hash: str,
        revoked_at: datetime,
    ) -> bool:
        del revoked_at
        stored = self.sessions.get(token_hash)
        if stored is None or stored.session_id != session_id:
            return False
        del self.sessions[token_hash]
        return True


class PlainPasswordVerifier:
    def __init__(self) -> None:
        self.verify_calls = 0

    def hash(self, password: str) -> str:
        return f"hash:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        self.verify_calls += 1
        return password_hash == self.hash(password)

    def needs_rehash(self, password_hash: str) -> bool:
        return password_hash.startswith("old:")


def _identity(*, status: UserStatus = UserStatus.ACTIVE) -> LocalIdentityRecord:
    return LocalIdentityRecord(
        "identity-identifier-1",
        "hash:correct password",
        AuthenticatedPrincipal(
            "user-identifier-1",
            "Owner",
            UserRole.ADMIN,
            status,
        ),
    )


def test_login_rotate_expire_and_logout_never_persist_raw_bearers() -> None:
    now = [datetime(2030, 1, 1, tzinfo=UTC)]
    secrets = iter(("s" * 43, "c" * 43, "n" * 43, "r" * 43))
    store = MemoryAuthStore(_identity())
    verifier = PlainPasswordVerifier()
    service = AuthSessionService(
        store,
        verifier,
        InMemoryLoginAttemptLimiter(attempt_limit=5, window_seconds=300),
        dummy_password_hash="hash:dummy",
        session_ttl_minutes=720,
        rotation_minutes=30,
        clock=lambda: now[0],
        secret_factory=lambda: next(secrets),
        id_factory=lambda: "session-identifier-1",
    )

    issued = asyncio.run(
        service.login(local_id=" OWNER ", password="correct password", source="127.0.0.1")
    )
    assert issued.view.principal.role is UserRole.ADMIN
    assert issued.view.expires_at == now[0] + timedelta(hours=12)
    assert store.created is not None
    assert store.created.token_hash == hash_bearer("s" * 43)
    assert store.created.csrf_hash == hash_bearer("c" * 43)
    assert "s" * 43 not in repr(store.created)
    assert "c" * 43 not in repr(store.created)

    now[0] += timedelta(minutes=31)
    assert asyncio.run(service.current_session(session_token="s" * 43)).rotation_due is True
    with pytest.raises(CsrfRejectedError):
        asyncio.run(service.rotate(session_token="s" * 43, csrf_token="wrong" * 10))

    rotated = asyncio.run(service.rotate(session_token="s" * 43, csrf_token="c" * 43))
    assert rotated.session_token == "n" * 43
    assert rotated.csrf_token == "r" * 43
    assert rotated.view.expires_at == issued.view.expires_at
    with pytest.raises(AuthenticationRequiredError):
        asyncio.run(service.current_session(session_token="s" * 43))

    asyncio.run(service.logout(session_token="n" * 43, csrf_token="r" * 43))
    with pytest.raises(AuthenticationRequiredError):
        asyncio.run(service.current_session(session_token="n" * 43))


def test_login_limiter_blocks_before_the_sixth_password_verification() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    verifier = PlainPasswordVerifier()
    service = AuthSessionService(
        MemoryAuthStore(None),
        verifier,
        InMemoryLoginAttemptLimiter(attempt_limit=5, window_seconds=300),
        dummy_password_hash="hash:dummy",
        session_ttl_minutes=720,
        rotation_minutes=30,
        clock=lambda: now,
    )

    for attempt in range(5):
        with pytest.raises(InvalidCredentialsError):
            asyncio.run(
                service.login(
                    local_id=f"missing-{attempt}",
                    password="wrong",
                    source="127.0.0.1",
                )
            )
    with pytest.raises(LoginRateLimitedError) as captured:
        asyncio.run(
            service.login(local_id="another-missing", password="wrong", source="127.0.0.1")
        )

    assert verifier.verify_calls == 5
    assert captured.value.retry_after_seconds == 300


def test_disabled_identity_uses_the_same_invalid_credentials_error() -> None:
    verifier = PlainPasswordVerifier()
    service = AuthSessionService(
        MemoryAuthStore(_identity(status=UserStatus.DISABLED)),
        verifier,
        InMemoryLoginAttemptLimiter(attempt_limit=5, window_seconds=300),
        dummy_password_hash="hash:dummy",
        session_ttl_minutes=720,
        rotation_minutes=30,
    )

    with pytest.raises(InvalidCredentialsError, match="invalid"):
        asyncio.run(
            service.login(
                local_id="owner",
                password="correct password",
                source="127.0.0.1",
            )
        )
