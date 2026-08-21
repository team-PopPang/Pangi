"""Build-time OpenAPI publication and drift contracts."""

from pathlib import Path

import pytest

from pangi.openapi import generate_openapi_document, render_openapi_document

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_OPENAPI_ARTIFACT = _PROJECT_ROOT / "docs" / "openapi" / "pangi-admin-api.json"


def test_openapi_has_stable_operations_and_error_schemas() -> None:
    document = generate_openapi_document()
    paths = document["paths"]
    assert isinstance(paths, dict)
    assert set(paths) == {
        "/api/v1/audit-events",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/session",
        "/api/v1/auth/session/rotate",
        "/api/v1/bootstrap/admin",
        "/api/v1/model-policies",
        "/api/v1/model-policies/{policy_id}/versions/{version}/activate",
        "/api/v1/model-policies/{policy_id}/versions/{version}/evaluate",
        "/api/v1/runs",
        "/api/v1/runs/metrics",
        "/api/v1/runs/{run_id}",
        "/api/v1/runs/{run_id}/cancel",
        "/api/v1/runs/{run_id}/events",
    }

    operation_ids = {
        operation["operationId"]
        for path_item in paths.values()
        if isinstance(path_item, dict)
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert operation_ids == {
        "createBootstrapAdmin",
        "getAuthSession",
        "login",
        "logout",
        "rotateAuthSession",
        "listModelPolicies",
        "evaluateModelPolicy",
        "activateModelPolicy",
        "createRun",
        "listRuns",
        "listAuditEvents",
        "getRunQueueMetrics",
        "getRun",
        "cancelRun",
        "getRunEvents",
    }

    documented_errors = {
        status
        for path_item in paths.values()
        if isinstance(path_item, dict)
        for operation in path_item.values()
        if isinstance(operation, dict)
        for status in operation.get("responses", {})
        if status != "200" and status != "201" and status != "204"
    }
    assert {"400", "401", "403", "404", "409", "422", "429", "500", "503"} <= (
        documented_errors
    )
    for path_item in paths.values():
        assert isinstance(path_item, dict)
        for operation in path_item.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            for status, response in operation["responses"].items():
                if int(status) < 400:
                    continue
                assert response["content"]["application/json"]["schema"] == {
                    "$ref": "#/components/schemas/ErrorEnvelope"
                }

    components = document["components"]
    assert isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    assert {
        "AuditEventListEnvelope",
        "BootstrapAdminRequest",
        "BootstrapAdminResponse",
        "ErrorEnvelope",
        "LoginRequest",
        "ModelPolicyActivateRequest",
        "ModelPolicyActivationEnvelope",
        "ModelPolicyEvaluateRequest",
        "ModelPolicyEvaluationEnvelope",
        "ModelPolicyListEnvelope",
        "RunCancellationEnvelope",
        "RunCreateRequest",
        "RunEnvelope",
        "RunEventListEnvelope",
        "RunListEnvelope",
        "RunQueueMetricsResponse",
        "RunSubmissionEnvelope",
        "SessionEnvelope",
    } <= set(schemas)
    assert schemas["BootstrapAdminRequest"]["properties"]["token"]["writeOnly"] is True
    assert schemas["BootstrapAdminRequest"]["properties"]["password"]["writeOnly"] is True
    assert schemas["LoginRequest"]["properties"]["password"]["writeOnly"] is True
    for schema_name, property_name in (
        ("BootstrapAdminRequest", "token"),
        ("BootstrapAdminRequest", "password"),
        ("LoginRequest", "password"),
    ):
        sensitive_schema = schemas[schema_name]["properties"][property_name]
        assert not {"default", "example", "examples"} & set(sensitive_schema)

    run_response = schemas["RunResponse"]["properties"]
    assert not {"worker_id", "lease_expires_at", "heartbeat_at"} & set(run_response)
    run_request = schemas["RunRequestResponse"]["properties"]
    assert "idempotency_key" not in run_request
    create_request = schemas["RunCreateRequest"]["properties"]
    assert set(create_request) == {"text", "thread_key", "explicit_skill"}
    assert not {
        "principal",
        "request_id",
        "created_at",
        "schedule_id",
        "attachments",
        "data_classes",
    } & set(create_request)

    event_response = paths["/api/v1/runs/{run_id}/events"]["get"]["responses"]["200"]
    assert set(event_response["content"]) == {
        "application/json",
        "text/event-stream",
    }


def test_openapi_generation_has_no_runtime_data_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    generate_openapi_document()

    assert list(tmp_path.iterdir()) == []


def test_committed_openapi_artifact_has_not_drifted() -> None:
    assert _OPENAPI_ARTIFACT.read_text("utf-8") == render_openapi_document()
