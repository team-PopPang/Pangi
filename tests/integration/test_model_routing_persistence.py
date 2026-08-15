"""Model Policy and Invocation SQLite integration tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.model_providers.json_schema import JsonSchemaOutputValidator
from pangi.adapters.outbound.persistence.sqlite.connection import fetch_all, fetch_one
from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.adapters.outbound.persistence.sqlite.event_writer import SqliteRunEventWriter
from pangi.adapters.outbound.persistence.sqlite.model_routing import (
    SqliteModelInvocationRecorder,
    SqliteModelPolicyRepository,
)
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.model_persistence import (
    ModelInvocationContext,
    ModelPolicySnapshot,
)
from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelCallRequest,
    ModelEgressPolicy,
    ModelInputSource,
    ModelPolicyBlockedError,
    ModelProfile,
    ModelProviderResponse,
    ModelTokenUsage,
    StructuredOutputSchema,
)
from pangi.application.ports.model_persistence import ModelInvocationPersistenceError
from pangi.application.services.model_routing import (
    GuardedModelExecutionService,
    ModelPolicyService,
)
from pangi.application.services.redaction import (
    RedactionService,
    core_secret_redaction_policy,
)
from pangi.application.services.telemetry_redaction import (
    core_telemetry_redaction_service,
)
from pangi.domain.model_routing import (
    DataClass,
    ModelPurpose,
    ModelRetention,
)
from pangi.domain.runs import RunEvent

NOW = datetime(2030, 1, 1, tzinfo=UTC)
RUN_ID = "run-identifier-0001"
STEP_ID = "step-identifier-0001"


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


async def _insert_run(database: SqliteDatabase) -> None:
    async with database.create() as unit_of_work:
        timestamp = NOW.isoformat()
        await unit_of_work.connection.execute(
            "INSERT INTO users (id, display_name, role, status, created_at, updated_at) "
            "VALUES (?, ?, 'member', 'active', ?, ?)",
            ("member-user-00001", "Model Test", timestamp, timestamp),
        )
        await unit_of_work.connection.execute(
            "INSERT INTO runs "
            "(id, request_id, principal_id, trigger, state, request_text, idempotency_key, "
            "created_at, updated_at) VALUES (?, ?, ?, 'eval', 'received', ?, ?, ?, ?)",
            (
                RUN_ID,
                "model-request-0001",
                "member-user-00001",
                "safe Run request",
                "model-request-once",
                timestamp,
                timestamp,
            ),
        )
        await unit_of_work.connection.execute(
            "INSERT INTO run_steps "
            "(id, run_id, node_id, type, state, requirement, idempotent, attempt, "
            "created_at, updated_at) VALUES (?, ?, 'model-node', 'model', 'running', "
            "'required', 1, 1, ?, ?)",
            (STEP_ID, RUN_ID, timestamp, timestamp),
        )
        await unit_of_work.commit()


def _profile() -> ModelProfile:
    return ModelProfile(
        profile_id="root-openai-primary",
        profile="root-default",
        profile_version="profile-v1",
        provider="openai",
        model="gpt-5.6",
        region="us-east-1",
        supported_data_classes=frozenset({DataClass.INTERNAL}),
        supported_source_kinds=frozenset({"channel"}),
        supported_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        retention=ModelRetention.ZERO_RETENTION,
        allow_raw_content=False,
        routing_priority=1,
    )


def _policy(*, version: str = "policy-v1") -> ModelEgressPolicy:
    return ModelEgressPolicy(
        policy_id="root-default-egress",
        policy_version=version,
        profile="root-default",
        allowed_providers=frozenset({"openai"}),
        allowed_models=frozenset({"gpt-5.6"}),
        allowed_regions=frozenset({"us-east-1"}),
        allowed_data_classes=frozenset({DataClass.INTERNAL}),
        allowed_source_kinds=frozenset({"channel"}),
        allowed_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        require_redaction=True,
        require_zero_retention=True,
        allow_raw_content=False,
    )


def _request(
    logical_call_id: str,
    *,
    data_class: DataClass = DataClass.INTERNAL,
    content: str = "safe Model request",
) -> ModelCallRequest:
    return ModelCallRequest(
        logical_call_id=logical_call_id,
        profile="root-default",
        purpose=ModelPurpose.ORCHESTRATION,
        sources=(
            ModelInputSource(
                source_kind="channel",
                data_classes=frozenset({data_class}),
                content=content,
                raw_content=False,
            ),
        ),
        output_schema=StructuredOutputSchema(
            name="model-result-v1",
            canonical_schema_json=(
                '{"additionalProperties":false,"properties":{"answer":{"type":"string"}},'
                '"required":["answer"],"type":"object"}'
            ),
        ),
    )


class RecordingProvider:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[GuardedModelRequest] = []

    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        self.calls.append(request)
        return ModelProviderResponse(
            self.output,
            token_usage=ModelTokenUsage(12, 7, 19),
            provider_request_count=3,
            duration_ms=425,
            provider_latency_ms=200,
        )


def test_policy_drafts_active_reads_and_active_constraints(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            repository = SqliteModelPolicyRepository(database)
            first = ModelPolicySnapshot(_policy(), (_profile(),))
            await repository.save_draft(first, at=NOW)
            assert await repository.get_policy("root-default") is None
            assert await repository.list_candidates("root-default") == ()

            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "UPDATE model_policies SET state = 'active', updated_at = ? "
                    "WHERE id = ? AND version = ?",
                    (
                        (NOW + timedelta(seconds=1)).isoformat(),
                        first.policy.policy_id,
                        first.policy.policy_version,
                    ),
                )
                await unit_of_work.commit()

            assert await repository.get_policy("root-default") == first.policy
            assert await repository.list_candidates("root-default") == first.profiles

            second = ModelPolicySnapshot(_policy(version="policy-v2"), (_profile(),))
            await repository.save_draft(second, at=NOW + timedelta(minutes=1))
            async with database.create() as unit_of_work:
                with pytest.raises(aiosqlite.IntegrityError):
                    await unit_of_work.connection.execute(
                        "UPDATE model_policies SET state = 'active' "
                        "WHERE id = ? AND version = ?",
                        (second.policy.policy_id, second.policy.policy_version),
                    )

            async with database.create() as unit_of_work:
                with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
                    await unit_of_work.connection.execute(
                        "UPDATE model_policies SET rules_json = ? "
                        "WHERE id = ? AND version = ?",
                        (
                            second.rules_json,
                            first.policy.policy_id,
                            first.policy.policy_version,
                        ),
                    )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_allowed_denied_and_retry_measurements_are_secret_safe(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_run(database)
            policies = SqliteModelPolicyRepository(database)
            snapshot = ModelPolicySnapshot(_policy(), (_profile(),))
            await policies.save_draft(snapshot, at=NOW)
            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "UPDATE model_policies SET state = 'active' "
                    "WHERE id = ? AND version = ?",
                    (snapshot.policy.policy_id, snapshot.policy.policy_version),
                )
                await unit_of_work.commit()

            provider_output = "private-provider-output"
            provider = RecordingProvider(f'{{"answer":"{provider_output}"}}')
            ticks = iter(
                (
                    NOW + timedelta(seconds=1),
                    NOW + timedelta(seconds=2),
                    NOW + timedelta(seconds=3),
                    NOW + timedelta(seconds=4),
                )
            )
            identifiers = iter(
                (
                    "model-invocation-0001",
                    "model-invocation-0002",
                    "model-invocation-0003",
                )
            )
            service = GuardedModelExecutionService(
                ModelPolicyService(
                    profiles=policies,
                    policies=policies,
                    redactor=RedactionService(core_secret_redaction_policy()),
                ),
                provider=provider,
                output_validator=JsonSchemaOutputValidator(),
                invocations=SqliteModelInvocationRecorder(database),
                clock=lambda: next(ticks),
                id_factory=lambda: next(identifiers),
            )
            prompt_secret = "sk-model-prompt-secret-12345"
            result = await service.execute(
                _request("logical-call-secret", content=prompt_secret),
                context=ModelInvocationContext(RUN_ID, STEP_ID),
            )
            assert result.response.provider_request_count == 3
            assert prompt_secret not in provider.calls[0].sources[0].content

            with pytest.raises(ModelInvocationPersistenceError):
                await service.execute(
                    _request("logical-call-secret", content="duplicate logical call"),
                    context=ModelInvocationContext(RUN_ID, STEP_ID),
                )

            with pytest.raises(ModelPolicyBlockedError):
                await service.execute(
                    _request(
                        "logical-call-denied",
                        data_class=DataClass.RESTRICTED,
                    ),
                    context=ModelInvocationContext(RUN_ID, STEP_ID),
                )

            async with database.create() as unit_of_work:
                invocations = await fetch_all(
                    unit_of_work.connection,
                    "SELECT * FROM model_invocations ORDER BY created_at, id",
                )
                events = await fetch_all(
                    unit_of_work.connection,
                    "SELECT type, visibility, attributes_json FROM run_events "
                    "ORDER BY event_index",
                )
                await unit_of_work.commit()

            assert len(provider.calls) == 1
            assert len(invocations) == 2
            completed = next(row for row in invocations if row["state"] == "completed")
            denied = next(row for row in invocations if row["state"] == "denied")
            assert int(completed["logical_calls"]) == 1
            assert int(completed["provider_requests"]) == 3
            assert (
                int(completed["input_tokens"]),
                int(completed["output_tokens"]),
                int(completed["total_tokens"]),
            ) == (12, 7, 19)
            assert int(completed["duration_ms"]) == 425
            assert int(completed["provider_latency_ms"]) == 200
            assert str(completed["finish_reason"]) == "stop"
            assert int(denied["logical_calls"]) == 1
            assert int(denied["provider_requests"]) == 0
            assert str(denied["error_code"]) == "model_policy_denied"
            assert [str(row["type"]) for row in events] == [
                "model.policy_allowed",
                "model.invocation_completed",
                "model.policy_denied",
            ]
            assert {str(row["visibility"]) for row in events} == {"internal"}

            persisted = "\n".join(
                str(value)
                for row in (*invocations, *events)
                for value in tuple(row)
            )
            assert prompt_secret not in persisted
            assert provider_output not in persisted
            assert "logical-call-secret" not in persisted
            assert "logical-call-denied" not in persisted
        finally:
            await database.close()

    asyncio.run(scenario())


def test_start_event_failure_rolls_back_and_prevents_provider_call(tmp_path: Path) -> None:
    class FailingEventWriter(SqliteRunEventWriter):
        async def insert(
            self,
            connection: aiosqlite.Connection,
            event: RunEvent,
        ) -> RunEvent:
            raise aiosqlite.OperationalError("forced event failure")

    async def scenario() -> None:
        database = _database(tmp_path)
        await database.start()
        try:
            await _insert_run(database)
            policies = SqliteModelPolicyRepository(database)
            snapshot = ModelPolicySnapshot(_policy(), (_profile(),))
            await policies.save_draft(snapshot, at=NOW)
            async with database.create() as unit_of_work:
                await unit_of_work.connection.execute(
                    "UPDATE model_policies SET state = 'active' "
                    "WHERE id = ? AND version = ?",
                    (snapshot.policy.policy_id, snapshot.policy.policy_version),
                )
                await unit_of_work.commit()

            provider = RecordingProvider('{"answer":"safe"}')
            recorder = SqliteModelInvocationRecorder(
                database,
                FailingEventWriter(core_telemetry_redaction_service()),
            )
            service = GuardedModelExecutionService(
                ModelPolicyService(
                    profiles=policies,
                    policies=policies,
                    redactor=RedactionService(core_secret_redaction_policy()),
                ),
                provider=provider,
                output_validator=JsonSchemaOutputValidator(),
                invocations=recorder,
                clock=lambda: NOW,
                id_factory=lambda: "model-invocation-0001",
            )
            with pytest.raises(ModelInvocationPersistenceError):
                await service.execute(
                    _request("logical-call-rollback"),
                    context=ModelInvocationContext(RUN_ID, STEP_ID),
                )
            assert provider.calls == []
            async with database.create() as unit_of_work:
                invocation = await fetch_one(
                    unit_of_work.connection,
                    "SELECT id FROM model_invocations",
                )
                event = await fetch_one(
                    unit_of_work.connection,
                    "SELECT type FROM run_events",
                )
                await unit_of_work.commit()
            assert invocation is None
            assert event is None
        finally:
            await database.close()

    asyncio.run(scenario())
