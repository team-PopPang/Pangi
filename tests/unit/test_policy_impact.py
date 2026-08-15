"""Deterministic and secret-safe security-policy impact contracts."""

from dataclasses import replace

import pytest

from pangi.application.contracts.policy_impact import (
    POLICY_IMPACT_SCHEMA_VERSION,
    PolicyFingerprintReference,
    PolicyImpactDiff,
    PolicyImpactSnapshot,
    compare_policy_impact,
)
from pangi.application.services.audit import core_audit_policy
from pangi.application.services.redaction import core_secret_redaction_policy


def _reference(
    policy_kind: str,
    policy_id: str,
    *,
    policy_version: str = "policy-v1",
    fingerprint_character: str = "a",
) -> PolicyFingerprintReference:
    return PolicyFingerprintReference(
        policy_kind=policy_kind,
        policy_id=policy_id,
        policy_version=policy_version,
        policy_fingerprint=fingerprint_character * 64,
    )


def test_snapshot_fingerprint_is_canonical_and_order_independent() -> None:
    input_policy = _reference("guardrail.input", "core-input-v1")
    audit_policy = _reference(
        "audit",
        "core-audit-v1",
        fingerprint_character="b",
    )

    first = PolicyImpactSnapshot((input_policy, audit_policy))
    reordered = PolicyImpactSnapshot((audit_policy, input_policy))

    assert first == reordered
    assert first.fingerprint == reordered.fingerprint
    assert len(first.fingerprint) == 64
    assert first.schema_version == POLICY_IMPACT_SCHEMA_VERSION
    assert tuple(policy.key for policy in first.policies) == (
        "audit:core-audit-v1",
        "guardrail.input:core-input-v1",
    )
    assert first.as_dict()["fingerprint"] == first.fingerprint


def test_current_redaction_and_audit_policies_form_an_impact_snapshot() -> None:
    redaction = core_secret_redaction_policy()
    audit = core_audit_policy()

    snapshot = PolicyImpactSnapshot(
        (
            PolicyFingerprintReference(
                policy_kind="redaction",
                policy_id=redaction.policy_version,
                policy_version=redaction.policy_version,
                policy_fingerprint=redaction.fingerprint,
            ),
            PolicyFingerprintReference(
                policy_kind="audit",
                policy_id=audit.policy_version,
                policy_version=audit.policy_version,
                policy_fingerprint=audit.fingerprint,
            ),
        )
    )

    assert tuple(policy.key for policy in snapshot.policies) == (
        "audit:core-audit-v1",
        "redaction:core-secret-v1",
    )
    assert len(snapshot.fingerprint) == 64


def test_compare_identifies_added_removed_and_changed_policy_keys() -> None:
    baseline = PolicyImpactSnapshot(
        (
            _reference("audit", "core-audit-v1", fingerprint_character="a"),
            _reference("guardrail.input", "core-input-v1", fingerprint_character="b"),
            _reference("guardrail.output", "core-output-v1", fingerprint_character="c"),
        )
    )
    candidate = PolicyImpactSnapshot(
        (
            _reference(
                "guardrail.input",
                "core-input-v1",
                policy_version="policy-v2",
                fingerprint_character="d",
            ),
            _reference("guardrail.output", "core-output-v1", fingerprint_character="c"),
            _reference("redaction", "core-secret-v1", fingerprint_character="e"),
        )
    )

    impact = compare_policy_impact(baseline, candidate)

    assert baseline.fingerprint != candidate.fingerprint
    assert impact.added_policy_keys == ("redaction:core-secret-v1",)
    assert impact.removed_policy_keys == ("audit:core-audit-v1",)
    assert impact.changed_policy_keys == ("guardrail.input:core-input-v1",)
    assert impact.affected_policy_keys == (
        "audit:core-audit-v1",
        "guardrail.input:core-input-v1",
        "redaction:core-secret-v1",
    )
    assert impact.has_changes


def test_compare_reports_no_impact_for_equivalent_snapshots() -> None:
    baseline = PolicyImpactSnapshot((_reference("audit", "core-audit-v1"),))
    candidate = PolicyImpactSnapshot(tuple(reversed(baseline.policies)))

    impact = compare_policy_impact(baseline, candidate)

    assert not impact.has_changes
    assert impact.affected_policy_keys == ()
    assert impact.baseline_fingerprint == impact.candidate_fingerprint


def test_policy_version_or_fingerprint_change_changes_snapshot() -> None:
    reference = _reference("audit", "core-audit-v1")
    baseline = PolicyImpactSnapshot((reference,))
    version_changed = PolicyImpactSnapshot(
        (replace(reference, policy_version="policy-v2"),)
    )
    fingerprint_changed = PolicyImpactSnapshot(
        (replace(reference, policy_fingerprint="b" * 64),)
    )

    assert baseline.fingerprint != version_changed.fingerprint
    assert baseline.fingerprint != fingerprint_changed.fingerprint
    assert compare_policy_impact(baseline, version_changed).changed_policy_keys == (
        "audit:core-audit-v1",
    )
    assert compare_policy_impact(baseline, fingerprint_changed).changed_policy_keys == (
        "audit:core-audit-v1",
    )


def test_snapshot_rejects_mutable_empty_duplicate_or_invalid_inputs() -> None:
    reference = _reference("audit", "core-audit-v1")

    with pytest.raises(ValueError, match="immutable tuple"):
        PolicyImpactSnapshot([reference])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cannot be empty"):
        PolicyImpactSnapshot(())
    with pytest.raises(ValueError, match="duplicate policy keys"):
        PolicyImpactSnapshot((reference, replace(reference, policy_version="policy-v2")))
    with pytest.raises(ValueError, match="policy_kind"):
        _reference("Guardrail Input", "core-input-v1")
    with pytest.raises(ValueError, match="policy_id"):
        _reference("audit", "core audit")
    with pytest.raises(ValueError, match="policy_version"):
        _reference("audit", "core-audit-v1", policy_version="policy version")

    secret = "token=private-policy-value"
    with pytest.raises(ValueError) as captured:
        PolicyFingerprintReference(
            policy_kind="audit",
            policy_id="core-audit-v1",
            policy_version="policy-v1",
            policy_fingerprint=secret,
        )
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)


def test_snapshot_contract_cannot_accept_policy_bodies_or_connection_data() -> None:
    with pytest.raises(TypeError):
        PolicyFingerprintReference(
            policy_kind="tool",
            policy_id="linear.issue.create",
            policy_version="policy-v1",
            policy_fingerprint="a" * 64,
            connection_id="connection-secret",  # type: ignore[call-arg]
            rules={"allow": "private-rule"},  # type: ignore[call-arg]
        )


def test_impact_diff_rejects_mutable_overlapping_or_invalid_policy_keys() -> None:
    values: dict[str, object] = {
        "baseline_fingerprint": "a" * 64,
        "candidate_fingerprint": "b" * 64,
        "added_policy_keys": ("audit:core-audit-v1",),
        "removed_policy_keys": (),
        "changed_policy_keys": (),
    }

    with pytest.raises(ValueError, match="immutable tuple"):
        PolicyImpactDiff(**(values | {"added_policy_keys": []}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="disjoint"):
        PolicyImpactDiff(
            **(
                values
                | {
                    "changed_policy_keys": ("audit:core-audit-v1",),
                }
            )  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="must agree"):
        PolicyImpactDiff(
            **(
                values
                | {
                    "candidate_fingerprint": "a" * 64,
                }
            )  # type: ignore[arg-type]
        )

    secret = "token=private-policy-value"
    with pytest.raises(ValueError) as captured:
        PolicyImpactDiff(
            **(values | {"added_policy_keys": (secret,)})  # type: ignore[arg-type]
        )
    assert secret not in str(captured.value)
