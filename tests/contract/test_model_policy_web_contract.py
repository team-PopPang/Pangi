from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from pangi.adapters.inbound.web import create_web_app
from pangi.application.contracts.auth import AuthenticatedPrincipal, SessionView
from pangi.application.contracts.model_persistence import ModelPolicySnapshot
from pangi.application.contracts.model_policy_management import (
    ModelInvocationSummary,
    ModelPolicyActivation,
    ModelPolicyEvaluation,
    ModelPolicyListItem,
    ModelPolicyListPage,
    ModelPolicyVersion,
    compare_model_policy_versions,
)
from pangi.application.contracts.model_routing import ModelEgressPolicy, ModelProfile
from pangi.application.contracts.readiness import ReadinessReport
from pangi.application.ports.auth import PermissionDeniedError
from pangi.domain.auth import UserRole, UserStatus
from pangi.domain.model_routing import (
    DataClass,
    ModelPolicyState,
    ModelPurpose,
    ModelRetention,
)

NOW = datetime(2030, 1, 8, tzinfo=UTC)


class Runtime:
    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


class Readiness:
    def report(self) -> ReadinessReport:
        return ReadinessReport()


class AuthSessions:
    def __init__(self, role: UserRole) -> None:
        self.view = SessionView(
            AuthenticatedPrincipal(
                "admin-user-000001" if role is UserRole.ADMIN else "member-user-00001",
                "Actor",
                role,
                UserStatus.ACTIVE,
            ),
            NOW + timedelta(hours=12),
            NOW + timedelta(minutes=30),
            False,
        )

    async def current_session(self, *, session_token: str) -> SessionView:
        assert session_token == "s" * 43
        return self.view


class Unused:
    pass


def _policy_version() -> ModelPolicyVersion:
    profile = ModelProfile(
        profile_id="root-openai-primary",
        profile="root-default",
        profile_version="profile-v2",
        provider="openai",
        model="gpt-5.7",
        region="us-east-1",
        supported_data_classes=frozenset({DataClass.INTERNAL}),
        supported_source_kinds=frozenset({"channel"}),
        supported_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        retention=ModelRetention.ZERO_RETENTION,
        allow_raw_content=False,
        routing_priority=1,
    )
    policy = ModelEgressPolicy(
        policy_id="root-default-egress",
        policy_version="policy-v2",
        profile="root-default",
        allowed_providers=frozenset({"openai"}),
        allowed_models=frozenset({"gpt-5.7"}),
        allowed_regions=frozenset({"us-east-1"}),
        allowed_data_classes=frozenset({DataClass.INTERNAL}),
        allowed_source_kinds=frozenset({"channel"}),
        allowed_purposes=frozenset({ModelPurpose.ORCHESTRATION}),
        require_redaction=True,
        require_zero_retention=True,
        allow_raw_content=False,
    )
    return ModelPolicyVersion(
        ModelPolicySnapshot(policy, (profile,)),
        ModelPolicyState.DRAFT,
        None,
        NOW,
        NOW,
    )


class Operations:
    def __init__(self) -> None:
        self.policy = _policy_version()
        self.impact = compare_model_policy_versions(None, self.policy)
        self.evaluations = []
        self.activations = []

    @staticmethod
    def _admin(actor: AuthenticatedPrincipal) -> None:
        if actor.role is not UserRole.ADMIN:
            raise PermissionDeniedError("Administrator required")

    async def list_policies(self, *, actor, query):
        self._admin(actor)
        summary = ModelInvocationSummary(
            NOW - timedelta(days=7),
            NOW,
            8,
            2,
            (),
            (),
        )
        return ModelPolicyListPage(
            (ModelPolicyListItem(self.policy, summary, self.impact),),
            None,
        )

    async def evaluate_policy(
        self,
        *,
        actor,
        policy_id,
        version,
        candidate_fingerprint,
        idempotency_key,
    ):
        self._admin(actor)
        self.evaluations.append((policy_id, version, candidate_fingerprint, idempotency_key))
        return ModelPolicyEvaluation("eval-run-identifier-0001", "queued", self.impact)

    async def activate_policy(
        self,
        *,
        actor,
        policy_id,
        version,
        candidate_fingerprint,
        impact_fingerprint,
        eval_run_id,
        idempotency_key,
    ):
        self._admin(actor)
        self.activations.append(
            (
                policy_id,
                version,
                candidate_fingerprint,
                impact_fingerprint,
                eval_run_id,
                idempotency_key,
            )
        )
        return ModelPolicyActivation(
            ModelPolicyVersion(
                self.policy.snapshot,
                ModelPolicyState.ACTIVE,
                eval_run_id,
                self.policy.created_at,
                NOW,
            ),
            impact_fingerprint,
            False,
        )


def _static_root(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<h1>Pangi</h1>", "utf-8")
    return root


def _app(tmp_path: Path, *, role: UserRole, operations: Operations):
    unused = Unused()
    return create_web_app(
        runtime_backend=Runtime(),
        readiness_probe=Readiness(),
        audit_operations=unused,
        bootstrap_admin=unused,
        auth_sessions=AuthSessions(role),
        run_operations=unused,
        run_cancellations=unused,
        run_events=unused,
        run_queue_metrics=unused,
        model_policy_operations=operations,
        static_root=_static_root(tmp_path),
    )


def _authenticate(client: TestClient) -> None:
    client.cookies.set("pangi_session", "s" * 43)
    client.cookies.set("pangi_csrf", "c" * 32)


def _mutation_headers(idempotency_key: str) -> dict[str, str]:
    return {
        "Idempotency-Key": idempotency_key,
        "Origin": "http://127.0.0.1:8787",
        "X-CSRF-Token": "c" * 32,
    }


def test_admin_lists_safe_policy_impact_and_invocation_summary(tmp_path: Path) -> None:
    operations = Operations()
    with TestClient(
        _app(tmp_path, role=UserRole.ADMIN, operations=operations),
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
        _authenticate(client)
        response = client.get("/api/v1/model-policies")

    assert response.status_code == 200
    payload = response.json()["items"][0]
    assert payload["policy"]["state"] == "draft"
    assert payload["invocation_summary"]["allowed_count"] == 8
    assert payload["invocation_summary"]["denied_count"] == 2
    assert payload["impact"]["consumer_resolution"] == "unavailable"
    assert payload["impact"]["affected_consumers"] == []
    assert "rules_json" not in response.text
    assert "prompt" not in response.text.casefold()


def test_policy_mutations_require_csrf_idempotency_and_forward_exact_version(
    tmp_path: Path,
) -> None:
    operations = Operations()
    policy = operations.policy
    path = f"/api/v1/model-policies/{policy.policy_id}/versions/{policy.version}"
    with TestClient(
        _app(tmp_path, role=UserRole.ADMIN, operations=operations),
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
        _authenticate(client)
        missing_idempotency = client.post(
            path + "/evaluate",
            json={"candidate_fingerprint": policy.fingerprint},
            headers={
                "Origin": "http://127.0.0.1:8787",
                "X-CSRF-Token": "c" * 32,
            },
        )
        missing_csrf = client.post(
            path + "/evaluate",
            json={"candidate_fingerprint": policy.fingerprint},
            headers={"Idempotency-Key": "evaluate-once"},
        )
        evaluated = client.post(
            path + "/evaluate",
            json={"candidate_fingerprint": policy.fingerprint},
            headers=_mutation_headers("evaluate-once"),
        )
        evaluation = evaluated.json()
        activated = client.post(
            path + "/activate",
            json={
                "candidate_fingerprint": policy.fingerprint,
                "impact_fingerprint": evaluation["impact"]["impact_fingerprint"],
                "eval_run_id": evaluation["eval_run_id"],
            },
            headers=_mutation_headers("activate-once"),
        )

    assert missing_idempotency.status_code == 422
    assert missing_idempotency.json()["error"]["code"] == "invalid_request"
    assert missing_csrf.status_code == 403
    assert evaluated.status_code == 202
    assert activated.status_code == 200
    assert activated.json()["policy"]["state"] == "active"
    assert operations.evaluations[0] == (
        policy.policy_id,
        policy.version,
        policy.fingerprint,
        "evaluate-once",
    )
    assert operations.activations[0][-1] == "activate-once"


def test_model_policy_api_requires_active_admin(tmp_path: Path) -> None:
    operations = Operations()
    with TestClient(
        _app(tmp_path, role=UserRole.MEMBER, operations=operations),
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50000),
    ) as client:
        _authenticate(client)
        response = client.get("/api/v1/model-policies")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
