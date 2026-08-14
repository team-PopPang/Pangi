"""Local, read-only diagnostic checks."""

from __future__ import annotations

import os
import platform
import socket
import sys
from importlib import resources
from pathlib import Path

from pangi._version import __version__
from pangi.adapters.outbound.persistence.sqlite.doctor import build_sqlite_doctor_checks
from pangi.application.contracts.diagnostics import DiagnosticResult, DiagnosticStatus
from pangi.application.contracts.paths import RuntimePaths
from pangi.application.services.doctor import DoctorCheck, DoctorService
from pangi.config import PangiConfig


def _result(
    check_id: str,
    status: DiagnosticStatus,
    summary: str,
    next_command: str | None = None,
) -> DiagnosticResult:
    return DiagnosticResult(check_id, status, summary, next_command)


def _path_check(check_id: str, path: Path) -> DiagnosticResult:
    if not path.exists():
        return _result(check_id, DiagnosticStatus.FAIL, f"path does not exist: {path}")
    if not path.is_dir():
        return _result(check_id, DiagnosticStatus.FAIL, f"path is not a directory: {path}")
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        return _result(check_id, DiagnosticStatus.FAIL, f"path is not writable: {path}")
    return _result(check_id, DiagnosticStatus.PASS, f"writable: {path}")


def _file_check(check_id: str, path: Path) -> DiagnosticResult:
    if not path.is_file():
        return _result(check_id, DiagnosticStatus.FAIL, f"file does not exist: {path}")
    if not os.access(path, os.R_OK):
        return _result(check_id, DiagnosticStatus.FAIL, f"file is not readable: {path}")
    return _result(check_id, DiagnosticStatus.PASS, f"readable: {path}")


def _skip(check_id: str, summary: str) -> DiagnosticResult:
    return _result(check_id, DiagnosticStatus.SKIP, summary)


def build_doctor_service(paths: RuntimePaths, config: PangiConfig) -> DoctorService:
    """Compose checks available before SQLite and Web adapters exist."""

    def runtime_python() -> DiagnosticResult:
        supported = sys.version_info >= (3, 11)
        status = DiagnosticStatus.PASS if supported else DiagnosticStatus.FAIL
        return _result(
            "runtime.python",
            status,
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )

    def runtime_package() -> DiagnosticResult:
        return _result("runtime.package", DiagnosticStatus.PASS, f"Pangi {__version__}")

    def runtime_os() -> DiagnosticResult:
        name = platform.system()
        supported = name in {"Linux", "Darwin"}
        status = DiagnosticStatus.PASS if supported else DiagnosticStatus.FAIL
        return _result("runtime.os", status, name)

    def config_schema() -> DiagnosticResult:
        return _result(
            "config.schema",
            DiagnosticStatus.PASS,
            f"schema version {config.schema_version}",
        )

    def server_port() -> DiagnosticResult:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((config.server.host, config.server.port))
        except OSError:
            return _result(
                "process.port",
                DiagnosticStatus.FAIL,
                f"port is unavailable: {config.server.host}:{config.server.port}",
                "pangi config validate",
            )
        return _result(
            "process.port",
            DiagnosticStatus.PASS,
            f"port is available: {config.server.host}:{config.server.port}",
        )

    def product_integrity() -> DiagnosticResult:
        catalog = resources.files("pangi.builtins.resources").joinpath("catalog.json")
        assets = resources.files("pangi.web").joinpath("static/asset-manifest.json")
        if not catalog.is_file() or not assets.is_file():
            return _result(
                "product.integrity",
                DiagnosticStatus.FAIL,
                "packaged built-in resources are incomplete",
            )
        return _result(
            "product.integrity",
            DiagnosticStatus.PASS,
            "packaged built-in resources are present",
        )

    checks = (
        DoctorCheck("runtime.python", runtime_python),
        DoctorCheck("runtime.package", runtime_package),
        DoctorCheck("runtime.os", runtime_os),
        DoctorCheck("paths.config", lambda: _file_check("paths.config", paths.config_file)),
        DoctorCheck("paths.data", lambda: _path_check("paths.data", paths.data_dir)),
        DoctorCheck("paths.logs", lambda: _path_check("paths.logs", paths.log_dir)),
        DoctorCheck("paths.backups", lambda: _path_check("paths.backups", paths.backup_dir)),
        DoctorCheck("paths.vault", lambda: _path_check("paths.vault", paths.vault_dir)),
        DoctorCheck("config.schema", config_schema),
        *build_sqlite_doctor_checks(paths, config),
        DoctorCheck(
            "secrets.unavailable",
            lambda: _skip("secrets.unavailable", "Secret Store adapter is not configured"),
        ),
        DoctorCheck("process.port", server_port),
        DoctorCheck(
            "provider.unconfigured",
            lambda: _skip("provider.unconfigured", "Model Provider is not configured"),
            external=True,
        ),
        DoctorCheck(
            "slack.disabled",
            lambda: _skip("slack.disabled", "Slack is disabled"),
            external=True,
        ),
        DoctorCheck(
            "mcp.unconfigured",
            lambda: _skip("mcp.unconfigured", "MCP is not configured"),
            external=True,
        ),
        DoctorCheck("product.integrity", product_integrity),
    )
    return DoctorService(checks, pangi_version=__version__)
