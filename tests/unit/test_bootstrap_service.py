"""Bootstrap Admin application-service tests."""

import asyncio
from datetime import UTC, datetime

import pytest

from pangi.application.ports.bootstrap_admin import InvalidBootstrapGrantError
from pangi.application.services.bootstrap_admin import BootstrapAdminService


class RejectingStore:
    async def issue_grant(self, grant, *, rotate: bool):
        raise AssertionError("not used")

    async def validate_grant(self, *, token_hash: str, at: datetime) -> None:
        raise InvalidBootstrapGrantError("Bootstrap Grant is invalid or unavailable")

    async def create_admin(self, *, token_hash: str, admin):
        raise AssertionError("invalid Grant must not reach the write transaction")


class CountingPasswordHasher:
    def __init__(self) -> None:
        self.calls = 0

    def hash(self, password: str) -> str:
        self.calls += 1
        return "$argon2id$test"


def test_invalid_grant_is_rejected_before_expensive_password_hashing() -> None:
    hasher = CountingPasswordHasher()
    service = BootstrapAdminService(
        RejectingStore(),  # type: ignore[arg-type]
        hasher,
        public_base_url="http://127.0.0.1:8787",
        grant_ttl_minutes=30,
        clock=lambda: datetime(2026, 8, 15, tzinfo=UTC),
    )

    with pytest.raises(InvalidBootstrapGrantError):
        asyncio.run(
            service.create_admin(
                token="invalid-bootstrap-token-value-000001",
                local_id="owner",
                display_name="Owner",
                password="correct horse battery staple",
            )
        )

    assert hasher.calls == 0
