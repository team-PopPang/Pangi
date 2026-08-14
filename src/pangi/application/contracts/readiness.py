"""Stable runtime readiness contracts shared by HTTP and future probes."""

from dataclasses import dataclass
from enum import StrEnum


class ReadinessState(StrEnum):
    """Whether one required runtime capability can serve traffic."""

    READY = "READY"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True, slots=True)
class ReadinessCheckResult:
    """Secret-safe status for one required runtime capability."""

    check_id: str
    state: ReadinessState
    summary: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.check_id,
            "status": self.state.value,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Aggregate readiness response independent of Doctor checks."""

    checks: tuple[ReadinessCheckResult, ...]
    schema_version: int = 1

    @property
    def ready(self) -> bool:
        return bool(self.checks) and all(
            check.state is ReadinessState.READY for check in self.checks
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "product": "pangi",
            "status": "ready" if self.ready else "not_ready",
            "checks": [check.as_dict() for check in self.checks],
        }
