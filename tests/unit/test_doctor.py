"""Doctor ordering, offline behavior, and exit-code tests."""

from pangi.application.contracts.diagnostics import DiagnosticResult, DiagnosticStatus
from pangi.application.services.doctor import DoctorCheck, DoctorService


def _result(check_id: str, status: DiagnosticStatus) -> DiagnosticResult:
    return DiagnosticResult(check_id=check_id, status=status, summary="safe")


def test_offline_skips_external_check_without_calling_it() -> None:
    calls: list[str] = []

    def external() -> DiagnosticResult:
        calls.append("external")
        return _result("provider.default", DiagnosticStatus.PASS)

    service = DoctorService(
        (DoctorCheck("provider.default", external, external=True),),
        pangi_version="0.1.0",
    )

    report = service.run(offline=True)

    assert calls == []
    assert report.checks[0].status is DiagnosticStatus.SKIP
    assert report.exit_code() == 0


def test_fail_and_strict_warning_exit_codes_are_stable() -> None:
    warning = DoctorService(
        (DoctorCheck("warning", lambda: _result("warning", DiagnosticStatus.WARN)),),
        pangi_version="0.1.0",
    ).run()
    failure = DoctorService(
        (DoctorCheck("failure", lambda: _result("failure", DiagnosticStatus.FAIL)),),
        pangi_version="0.1.0",
    ).run()

    assert warning.exit_code() == 0
    assert warning.exit_code(strict=True) == 1
    assert failure.exit_code() == 1


def test_internal_check_error_is_sanitized_and_returns_code_two() -> None:
    def broken() -> DiagnosticResult:
        raise RuntimeError("token=must-not-escape")

    report = DoctorService(
        (DoctorCheck("broken", broken),),
        pangi_version="0.1.0",
    ).run()

    assert report.exit_code() == 2
    assert report.checks[0].summary == "diagnostic check failed internally"

