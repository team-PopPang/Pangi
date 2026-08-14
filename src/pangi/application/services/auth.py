"""Local login and persistent browser Session use cases."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta

from pangi.application.contracts.auth import (
    AuthenticatedPrincipal,
    IssuedSession,
    PasswordHashUpdate,
    SessionView,
    StoredAuthSession,
)
from pangi.application.ports.auth import (
    AuthenticationRequiredError,
    AuthSessionPort,
    AuthSessionStore,
    CsrfRejectedError,
    InvalidCredentialsError,
    LoginAttemptLimiter,
    LoginRateLimitedError,
    PasswordVerifier,
    PermissionDeniedError,
)
from pangi.domain.auth import AuthSession, UserRole, UserStatus, normalize_local_subject

Clock = Callable[[], datetime]
SecretFactory = Callable[[], str]
IdFactory = Callable[[], str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _secret() -> str:
    return secrets.token_urlsafe(32)


def _identifier() -> str:
    return uuid.uuid4().hex


def hash_bearer(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ensure_role(
    principal: AuthenticatedPrincipal,
    allowed_roles: Collection[UserRole],
) -> AuthenticatedPrincipal:
    """Return an allowed Principal or raise a stable authorization failure."""

    if principal.role not in allowed_roles:
        raise PermissionDeniedError("The authenticated role is not allowed")
    return principal


class AuthSessionService(AuthSessionPort):
    def __init__(
        self,
        store: AuthSessionStore,
        password_verifier: PasswordVerifier,
        login_limiter: LoginAttemptLimiter,
        *,
        dummy_password_hash: str,
        session_ttl_minutes: int,
        rotation_minutes: int,
        clock: Clock = _utc_now,
        secret_factory: SecretFactory = _secret,
        id_factory: IdFactory = _identifier,
    ) -> None:
        self._store = store
        self._password_verifier = password_verifier
        self._login_limiter = login_limiter
        self._dummy_password_hash = dummy_password_hash
        self._session_ttl = timedelta(minutes=session_ttl_minutes)
        self._rotation_interval = timedelta(minutes=rotation_minutes)
        self._clock = clock
        self._secret_factory = secret_factory
        self._id_factory = id_factory

    async def login(
        self,
        *,
        local_id: str,
        password: str,
        source: str,
    ) -> IssuedSession:
        now = self._clock().astimezone(UTC)
        try:
            subject = normalize_local_subject(local_id)
        except ValueError:
            subject = local_id.strip().casefold()
            valid_subject = False
        else:
            valid_subject = True
        limiter_keys = (f"source:{source}", f"identity:{source}:{subject}")
        for limiter_key in limiter_keys:
            retry_after = self._login_limiter.reserve(limiter_key, at=now)
            if retry_after is not None:
                raise LoginRateLimitedError(retry_after)

        identity = (
            await self._store.find_local_identity(subject=subject)
            if valid_subject
            else None
        )
        encoded_hash = (
            identity.password_hash if identity is not None else self._dummy_password_hash
        )
        verified = self._password_verifier.verify(encoded_hash, password)
        if (
            not verified
            or identity is None
            or identity.principal.status is not UserStatus.ACTIVE
        ):
            raise InvalidCredentialsError("The local credentials are invalid")

        password_update = None
        if self._password_verifier.needs_rehash(identity.password_hash):
            password_update = PasswordHashUpdate(
                identity_id=identity.identity_id,
                previous_hash=identity.password_hash,
                updated_hash=self._password_verifier.hash(password),
            )
        raw_token = self._secret_factory()
        raw_csrf = self._secret_factory()
        session = AuthSession(
            id=self._id_factory(),
            user_id=identity.principal.user_id,
            token_hash=hash_bearer(raw_token),
            csrf_hash=hash_bearer(raw_csrf),
            expires_at=now + self._session_ttl,
            created_at=now,
        )
        for limiter_key in limiter_keys:
            self._login_limiter.clear(limiter_key)
        await self._store.create_session(session, password_update=password_update)
        stored = StoredAuthSession(
            session_id=session.id,
            csrf_hash=session.csrf_hash,
            expires_at=session.expires_at,
            created_at=session.created_at,
            rotated_at=None,
            principal=identity.principal,
        )
        return IssuedSession(raw_token, raw_csrf, self._view(stored, at=now))

    async def current_session(self, *, session_token: str) -> SessionView:
        stored, now = await self._authenticate(session_token)
        return self._view(stored, at=now)

    async def rotate(
        self,
        *,
        session_token: str,
        csrf_token: str,
    ) -> IssuedSession:
        stored, now = await self._authenticate(session_token)
        self._validate_csrf(stored, csrf_token)
        raw_token = self._secret_factory()
        raw_csrf = self._secret_factory()
        previous_hash = hash_bearer(session_token)
        token_hash = hash_bearer(raw_token)
        csrf_hash = hash_bearer(raw_csrf)
        rotated = await self._store.rotate_session(
            session_id=stored.session_id,
            previous_token_hash=previous_hash,
            token_hash=token_hash,
            csrf_hash=csrf_hash,
            rotated_at=now,
        )
        if not rotated:
            raise AuthenticationRequiredError("An active Session is required")
        updated = StoredAuthSession(
            session_id=stored.session_id,
            csrf_hash=csrf_hash,
            expires_at=stored.expires_at,
            created_at=stored.created_at,
            rotated_at=now,
            principal=stored.principal,
        )
        return IssuedSession(raw_token, raw_csrf, self._view(updated, at=now))

    async def logout(self, *, session_token: str, csrf_token: str) -> None:
        stored, now = await self._authenticate(session_token)
        self._validate_csrf(stored, csrf_token)
        revoked = await self._store.revoke_session(
            session_id=stored.session_id,
            token_hash=hash_bearer(session_token),
            revoked_at=now,
        )
        if not revoked:
            raise AuthenticationRequiredError("An active Session is required")

    async def _authenticate(self, session_token: str) -> tuple[StoredAuthSession, datetime]:
        if not 20 <= len(session_token) <= 256:
            raise AuthenticationRequiredError("An active Session is required")
        now = self._clock().astimezone(UTC)
        stored = await self._store.get_session(
            token_hash=hash_bearer(session_token),
            at=now,
        )
        if stored is None or stored.principal.status is not UserStatus.ACTIVE:
            raise AuthenticationRequiredError("An active Session is required")
        return stored, now

    @staticmethod
    def _validate_csrf(stored: StoredAuthSession, csrf_token: str) -> None:
        if not 20 <= len(csrf_token) <= 256 or not secrets.compare_digest(
            stored.csrf_hash,
            hash_bearer(csrf_token),
        ):
            raise CsrfRejectedError("The CSRF token is invalid")

    def _view(self, stored: StoredAuthSession, *, at: datetime) -> SessionView:
        last_rotation = stored.rotated_at or stored.created_at
        rotation_due_at = last_rotation + self._rotation_interval
        return SessionView(
            principal=stored.principal,
            expires_at=stored.expires_at,
            rotation_due_at=rotation_due_at,
            rotation_due=at >= rotation_due_at,
        )
