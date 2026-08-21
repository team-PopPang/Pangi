"""SQLite Policy, selected Provider, Catalog, and Root composition integration tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.model_providers import router as provider_router
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.model_routing import (
    SqliteModelPolicyRepository,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.guardrails import GuardedRunRequest, GuardrailDecision
from pangi.application.contracts.model_persistence import ModelPolicySnapshot
from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelEgressPolicy,
    ModelPolicyBlockedError,
    ModelProfile,
    ModelProviderResponse,
    ProviderRetryPolicy,
)
from pangi.application.contracts.root_orchestration import RootOrchestrationRequest
from pangi.bootstrap import build_root_orchestrator_service
from pangi.domain.auth import UserRole
from pangi.domain.guardrails import GuardrailOutcome, GuardrailStage, TrustLevel
from pangi.domain.model_routing import DataClass, ModelPurpose, ModelRetention
from pangi.domain.runs import Principal, PrincipalChannel, RunMode, RunRequest

NOW = datetime(2030, 1, 1, tzinfo=UTC)
RUN_ID = "run-root-runtime-0001"


def _database(tmp_path: Path) -> tuple[SqliteDatabase, PangiConfig]:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig()
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    return SqliteDatabase(paths, config.storage), config


async def _insert_run(database: SqliteDatabase) -> None:
    timestamp = NOW.isoformat()
    async with database.create() as unit_of_work:
        await unit_of_work.connection.execute(
            "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
            "VALUES ('member-root-runtime', 'Root Runtime', 'member', 'active', ?, ?)",
            (timestamp, timestamp),
        )
        await unit_of_work.connection.execute(
            "INSERT INTO runs "
            "(id, request_id, principal_id, trigger, state, request_text, idempotency_key, "
            "created_at, updated_at) VALUES (?, 'request-root-runtime', "
            "'member-root-runtime', 'api', 'planning', 'safe request', "
            "'root-runtime-once', ?, ?)",
            (RUN_ID, timestamp, timestamp),
        )
        await unit_of_work.commit()


def _profile() -> ModelProfile:
    return ModelProfile(
        profile_id="root-openai-runtime",
        profile="root-default",
        profile_version="profile-v1",
        provider="openai",
        model="runtime-model",
        region="us-east-1",
        supported_data_classes=frozenset({DataClass.INTERNAL}),
        supported_source_kinds=frozenset({"channel", "policy"}),
        supported_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        retention=ModelRetention.ZERO_RETENTION,
        allow_raw_content=True,
        routing_priority=1,
    )


def _policy() -> ModelEgressPolicy:
    return ModelEgressPolicy(
        policy_id="root-egress-runtime",
        policy_version="policy-v1",
        profile="root-default",
        allowed_providers=frozenset({"openai"}),
        allowed_models=frozenset({"runtime-model"}),
        allowed_regions=frozenset({"us-east-1"}),
        allowed_data_classes=frozenset({DataClass.INTERNAL}),
        allowed_source_kinds=frozenset({"channel", "policy"}),
        allowed_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        require_redaction=True,
        require_zero_retention=True,
        allow_raw_content=True,
    )


def _request(secret: str = "safe request") -> RootOrchestrationRequest:
    run_request = RunRequest(
        request_id="request-root-runtime",
        principal=Principal(
            "member-root-runtime",
            UserRole.MEMBER,
            PrincipalChannel.API,
        ),
        text=f"Summarize {secret}",
        idempotency_key="root-runtime-once",
        created_at=NOW,
    )
    decision = GuardrailDecision(
        trust_level=TrustLevel.UNTRUSTED,
        stage=GuardrailStage.COMPLETE,
        outcome=GuardrailOutcome.ALLOWED,
        policy_version="input-v1",
        policy_fingerprint="a" * 64,
        unicode_policy_version="unicode-v1",
        text_bytes=len(run_request.text.encode()),
    )
    return RootOrchestrationRequest(
        run_id=RUN_ID,
        guarded_request=GuardedRunRequest(run_request, decision),
        data_classes=frozenset({DataClass.INTERNAL}),
    )


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[GuardedModelRequest] = []

    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        self.calls.append(request)
        output = {
            "composition": "deterministic",
            "direct_answer": "The request was handled safely.",
            "mode": "direct",
            "skill_name": None,
            "tasks": [],
            "user_message": None,
        }
        return ModelProviderResponse(
            json.dumps(output, separators=(",", ":"), sort_keys=True),
            provider_request_count=2,
        )


def test_composed_root_uses_active_policy_and_selected_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        database, config = _database(tmp_path)
        await database.start()
        try:
            await _insert_run(database)
            repository = SqliteModelPolicyRepository(database)
            snapshot = ModelPolicySnapshot(_policy(), (_profile(),))
            await repository.save_draft(snapshot, at=NOW)
            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "UPDATE model_policies SET state = 'active' WHERE id = ? AND version = ?",
                    (snapshot.policy.policy_id, snapshot.policy.policy_version),
                )
                await unit_of_work.commit()

            provider = RecordingProvider()
            factory_policies: list[ProviderRetryPolicy] = []

            def create_provider(*, retry_policy: ProviderRetryPolicy) -> RecordingProvider:
                factory_policies.append(retry_policy)
                return provider

            monkeypatch.setattr(provider_router, "create_openai_provider", create_provider)
            service = build_root_orchestrator_service(database, config)
            secret = "sk-root-runtime-secret-123456789"

            result = await service.decide(_request(secret))

            assert result.plan.decision.mode is RunMode.DIRECT
            assert result.logical_call_count == 1
            assert result.provider_request_count == 2
            assert len(factory_policies) == 1
            assert factory_policies[0].max_attempts == config.model.max_attempts
            assert len(provider.calls) == 1
            assert secret not in provider.calls[0].sources[1].content

            async with database.create() as unit_of_work:
                invocations = await fetch_all(
                    unit_of_work.connection,
                    "SELECT state, provider, model, region, provider_requests "
                    "FROM model_invocations",
                )
                await unit_of_work.commit()
            assert [tuple(row) for row in invocations] == [
                ("completed", "openai", "runtime-model", "us-east-1", 2)
            ]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_composed_root_denies_missing_policy_before_provider_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        database, config = _database(tmp_path)
        await database.start()
        try:
            await _insert_run(database)
            factory_calls = 0

            def unexpected_provider(*, retry_policy: ProviderRetryPolicy) -> RecordingProvider:
                nonlocal factory_calls
                del retry_policy
                factory_calls += 1
                return RecordingProvider()

            monkeypatch.setattr(provider_router, "create_openai_provider", unexpected_provider)
            service = build_root_orchestrator_service(database, config)

            with pytest.raises(ModelPolicyBlockedError):
                await service.decide(_request())

            assert factory_calls == 0
            async with database.create() as unit_of_work:
                invocations = await fetch_all(
                    unit_of_work.connection,
                    "SELECT state, provider_requests, error_code FROM model_invocations",
                )
                await unit_of_work.commit()
            assert [tuple(row) for row in invocations] == [
                ("denied", 0, "model_policy_missing")
            ]
        finally:
            await database.close()

    asyncio.run(scenario())
