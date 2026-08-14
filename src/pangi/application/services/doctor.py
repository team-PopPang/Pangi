"""Read-only ordered diagnostic execution."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from pangi.application.contracts.diagnostics import (
    DiagnosticResult,
    DiagnosticStatus,
    DoctorReport,
)

DiagnosticRunner = Callable[[], DiagnosticResult]


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    check_id: str
    runner: DiagnosticRunner
    external: bool = False


class DoctorService:
    """Run diagnostic checks in dependency order without changing state."""

    def __init__(self, checks: Iterable[DoctorCheck], *, pangi_version: str) -> None:
        self._checks = tuple(checks)
        self._pangi_version = pangi_version

    def run(self, *, offline: bool = False) -> DoctorReport:
        results: list[DiagnosticResult] = []
        internal_error = False
        for check in self._checks:
            if offline and check.external:
                results.append(
                    DiagnosticResult(
                        check_id=check.check_id,
                        status=DiagnosticStatus.SKIP,
                        summary="skipped in offline mode",
                    )
                )
                continue
            try:
                result = check.runner()
                if result.check_id != check.check_id:
                    raise ValueError("diagnostic check returned a mismatched id")
                results.append(result)
            except Exception:
                internal_error = True
                results.append(
                    DiagnosticResult(
                        check_id=check.check_id,
                        status=DiagnosticStatus.FAIL,
                        summary="diagnostic check failed internally",
                        next_command="pangi doctor --offline",
                    )
                )
        return DoctorReport(
            schema_version=1,
            pangi_version=self._pangi_version,
            checks=tuple(results),
            internal_error=internal_error,
        )
