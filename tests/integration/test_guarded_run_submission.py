"""Input guardrail integration with the existing transactional Run store."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.input_rate_limits import InMemoryInputRateLimiter
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.runs import SqliteRunStore
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.guardrails import (
    ExplicitSkillAccess,
    GuardrailBlockedError,
    InputGuardrailPolicy,
)
from pangi.application.services.input_guardrails import (
    GuardedRunSubmissionService,
    InputGuardrailService,
)
from pangi.application.services.runs import RunService
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.guardrails import GuardrailErrorCode
from pangi.domain.runs import AttachmentRef, Principal, PrincipalChannel, RunRequest

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class AllowSkills:
    async def check_access(
        self,
        *,
        actor: AuthenticatedPrincipal,
        explicit_skill: str,
    ) -> ExplicitSkillAccess:
        return ExplicitSkillAccess.ALLOWED


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
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "member-user-00001",
                "Member",
                UserRole.MEMBER.value,
                UserStatus.ACTIVE.value,
                timestamp,
                timestamp,
            ),
        )
        await unit_of_work.commit()


def _policy() -> InputGuardrailPolicy:
    return InputGuardrailPolicy(
        policy_version="input-v1",
        unicode_policy_version="unicode-v1",
        max_text_bytes=1_024,
        max_attachment_count=2,
        max_attachment_bytes=1_024,
        max_total_attachment_bytes=2_048,
        allowed_media_types=frozenset({"text/plain"}),
        prohibited_codepoints=frozenset({0x200B, 0x202E}),
        rate_limit=10,
        rate_window_seconds=60,
    )


def _request(*, request_id: str, text: str, created_at: datetime) -> RunRequest:
    return RunRequest(
        request_id=request_id,
        principal=Principal(
            "member-user-00001",
            UserRole.MEMBER,
            PrincipalChannel.DASHBOARD,
        ),
        text=text,
        idempotency_key="guarded-request-once",
        created_at=created_at,
        attachments=(
            AttachmentRef(
                "opaque-attachment-1",
                media_type="TEXT/PLAIN",
                size_bytes=12,
            ),
        ),
    )


def test_only_admitted_normalized_requests_reach_transactional_run_creation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_user(database)
            identifiers = iter(("run-identifier-0001", "run-identifier-0002"))
            guardrail = InputGuardrailService(
                _policy(),
                skill_authorizer=AllowSkills(),
                rate_limiter=InMemoryInputRateLimiter(max_keys=100),
                clock=lambda: NOW,
            )
            submission = GuardedRunSubmissionService(
                guardrail,
                run_creator=RunService(
                    SqliteRunStore(database),
                    clock=lambda: NOW,
                    id_factory=lambda: next(identifiers),
                ),
            )
            actor = AuthenticatedPrincipal(
                "member-user-00001",
                "Member",
                UserRole.MEMBER,
                UserStatus.ACTIVE,
            )

            with pytest.raises(GuardrailBlockedError) as blocked:
                await submission.submit(
                    actor=AuthenticatedPrincipal(
                        "disabled-user-00001",
                        "Disabled",
                        UserRole.MEMBER,
                        UserStatus.DISABLED,
                    ),
                    request=_request(
                        request_id="request-identifier-0",
                        text="차단될 요청",
                        created_at=NOW,
                    ),
                    route_key="runs.create",
                )
            assert blocked.value.code is GuardrailErrorCode.PRINCIPAL_INACTIVE
            await _assert_counts(database, expected=0)

            first = await submission.submit(
                actor=actor,
                request=_request(
                    request_id="request-identifier-1",
                    text="Cafe\u0301\r\n요약해줘",
                    created_at=NOW,
                ),
                route_key="runs.create",
            )
            replay = await submission.submit(
                actor=actor,
                request=_request(
                    request_id="request-identifier-2",
                    text="Café\n요약해줘",
                    created_at=NOW + timedelta(seconds=1),
                ),
                route_key="runs.create",
            )

            assert not first.creation.replayed
            assert replay.creation.replayed
            assert replay.creation.run.id == first.creation.run.id
            assert first.creation.run.request.text == "Café\n요약해줘"
            assert first.creation.run.request.attachments[0].media_type == "text/plain"
            await _assert_counts(database, expected=1)
        finally:
            await database.close()

    asyncio.run(scenario())


async def _assert_counts(database: SqliteDatabase, *, expected: int) -> None:
    async with database.create() as unit_of_work:
        for table in ("runs", "run_events", "api_idempotency_records"):
            row = await fetch_one(unit_of_work.connection, f"SELECT COUNT(*) FROM {table}")
            assert row is not None
            assert int(row[0]) == expected
        await unit_of_work.commit()
