"""Stable doctor result and exit-code contracts."""

from dataclasses import dataclass
from enum import StrEnum


class DiagnosticStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    check_id: str
    status: DiagnosticStatus
    summary: str
    next_command: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "id": self.check_id,
            "status": self.status.value,
            "summary": self.summary,
            "next_command": self.next_command,
        }


@dataclass(frozen=True, slots=True)
class DoctorReport:
    schema_version: int
    pangi_version: str
    checks: tuple[DiagnosticResult, ...]
    internal_error: bool = False

    def exit_code(self, *, strict: bool = False) -> int:
        if self.internal_error:
            return 2
        statuses = {check.status for check in self.checks}
        if DiagnosticStatus.FAIL in statuses:
            return 1
        if strict and DiagnosticStatus.WARN in statuses:
            return 1
        return 0

    def as_dict(self, *, strict: bool = False) -> dict[str, object]:
        counts = {
            status.value: sum(check.status is status for check in self.checks)
            for status in DiagnosticStatus
        }
        return {
            "schema_version": self.schema_version,
            "pangi_version": self.pangi_version,
            "status": "FAIL" if self.exit_code(strict=strict) else "PASS",
            "exit_code": self.exit_code(strict=strict),
            "counts": counts,
            "checks": [check.as_dict() for check in self.checks],
        }

