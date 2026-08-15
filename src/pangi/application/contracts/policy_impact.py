"""Secret-safe contracts for deterministic security-policy impact tracking."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

POLICY_IMPACT_SCHEMA_VERSION = "policy-impact-v1"

_POLICY_KIND = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_POLICY_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _validate_fingerprint(value: str, *, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")


def _validate_policy_key(value: str, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must contain policy key strings")
    policy_kind, separator, policy_id = value.partition(":")
    if (
        separator != ":"
        or ":" in policy_id
        or _POLICY_KIND.fullmatch(policy_kind) is None
        or _POLICY_IDENTIFIER.fullmatch(policy_id) is None
    ):
        raise ValueError(f"{field_name} contains an invalid policy key")


@dataclass(frozen=True, slots=True)
class PolicyFingerprintReference:
    """One safe reference to a versioned policy without its rules or source data."""

    policy_kind: str
    policy_id: str
    policy_version: str
    policy_fingerprint: str

    def __post_init__(self) -> None:
        if _POLICY_KIND.fullmatch(self.policy_kind) is None:
            raise ValueError("policy_kind must be a stable lowercase identifier")
        for value, field_name in (
            (self.policy_id, "policy_id"),
            (self.policy_version, "policy_version"),
        ):
            if _POLICY_IDENTIFIER.fullmatch(value) is None:
                raise ValueError(f"{field_name} must be a stable identifier")
        _validate_fingerprint(
            self.policy_fingerprint,
            field_name="policy_fingerprint",
        )

    @property
    def key(self) -> str:
        return f"{self.policy_kind}:{self.policy_id}"

    def as_dict(self) -> dict[str, str]:
        return {
            "policy_kind": self.policy_kind,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class PolicyImpactSnapshot:
    """A canonical immutable set of active policy fingerprints for Eval impact."""

    policies: tuple[PolicyFingerprintReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policies, tuple):
            raise ValueError("policies must be an immutable tuple")
        if not self.policies:
            raise ValueError("policies cannot be empty")
        if any(not isinstance(policy, PolicyFingerprintReference) for policy in self.policies):
            raise TypeError("policies must contain PolicyFingerprintReference values")

        ordered = tuple(sorted(self.policies, key=lambda policy: policy.key))
        keys = tuple(policy.key for policy in ordered)
        if len(keys) != len(set(keys)):
            raise ValueError("policies cannot contain duplicate policy keys")
        object.__setattr__(self, "policies", ordered)

    @property
    def schema_version(self) -> str:
        return POLICY_IMPACT_SCHEMA_VERSION

    @property
    def fingerprint(self) -> str:
        payload = {
            "policies": [policy.as_dict() for policy in self.policies],
            "schema_version": self.schema_version,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "fingerprint": self.fingerprint,
            "policies": [policy.as_dict() for policy in self.policies],
        }


@dataclass(frozen=True, slots=True)
class PolicyImpactDiff:
    """Safe policy keys changed between a baseline and a candidate snapshot."""

    baseline_fingerprint: str
    candidate_fingerprint: str
    added_policy_keys: tuple[str, ...]
    removed_policy_keys: tuple[str, ...]
    changed_policy_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_fingerprint(
            self.baseline_fingerprint,
            field_name="baseline_fingerprint",
        )
        _validate_fingerprint(
            self.candidate_fingerprint,
            field_name="candidate_fingerprint",
        )
        collections = (
            (self.added_policy_keys, "added_policy_keys"),
            (self.removed_policy_keys, "removed_policy_keys"),
            (self.changed_policy_keys, "changed_policy_keys"),
        )
        for values, field_name in collections:
            if not isinstance(values, tuple):
                raise ValueError(f"{field_name} must be an immutable tuple")
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{field_name} must contain unique sorted policy keys")
            for value in values:
                _validate_policy_key(value, field_name=field_name)

        added = set(self.added_policy_keys)
        removed = set(self.removed_policy_keys)
        changed = set(self.changed_policy_keys)
        if added & removed or added & changed or removed & changed:
            raise ValueError("policy impact categories must be disjoint")
        has_changes = bool(added or removed or changed)
        fingerprints_changed = self.baseline_fingerprint != self.candidate_fingerprint
        if has_changes != fingerprints_changed:
            raise ValueError("policy impact keys and snapshot fingerprints must agree")

    @property
    def affected_policy_keys(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                (
                    *self.added_policy_keys,
                    *self.removed_policy_keys,
                    *self.changed_policy_keys,
                )
            )
        )

    @property
    def has_changes(self) -> bool:
        return bool(self.affected_policy_keys)

    def as_dict(self) -> dict[str, object]:
        return {
            "baseline_fingerprint": self.baseline_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
            "added_policy_keys": list(self.added_policy_keys),
            "removed_policy_keys": list(self.removed_policy_keys),
            "changed_policy_keys": list(self.changed_policy_keys),
            "affected_policy_keys": list(self.affected_policy_keys),
            "has_changes": self.has_changes,
        }


def compare_policy_impact(
    baseline: PolicyImpactSnapshot,
    candidate: PolicyImpactSnapshot,
) -> PolicyImpactDiff:
    """Compare two canonical snapshots without reading policy bodies."""

    baseline_by_key = {policy.key: policy for policy in baseline.policies}
    candidate_by_key = {policy.key: policy for policy in candidate.policies}
    baseline_keys = set(baseline_by_key)
    candidate_keys = set(candidate_by_key)
    shared_keys = baseline_keys & candidate_keys

    return PolicyImpactDiff(
        baseline_fingerprint=baseline.fingerprint,
        candidate_fingerprint=candidate.fingerprint,
        added_policy_keys=tuple(sorted(candidate_keys - baseline_keys)),
        removed_policy_keys=tuple(sorted(baseline_keys - candidate_keys)),
        changed_policy_keys=tuple(
            sorted(
                key
                for key in shared_keys
                if baseline_by_key[key] != candidate_by_key[key]
            )
        ),
    )
