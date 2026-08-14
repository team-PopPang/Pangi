"""Local system-check integration tests."""

import socket
from pathlib import Path

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.adapters.outbound.system_checks import build_doctor_service
from pangi.config import ServerConfig


def test_port_conflict_returns_fail_with_safe_next_command(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(
        explicit_home=tmp_path / "runtime",
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    initializer = FileSystemInitializer()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        config = PangiConfig(server=ServerConfig(port=port))
        initializer.apply(initializer.plan(paths), config.to_toml())

        report = build_doctor_service(paths, config).run(offline=True)

    port_check = next(check for check in report.checks if check.check_id == "process.port")
    assert port_check.status.value == "FAIL"
    assert port_check.next_command == "pangi config validate"
    assert report.exit_code() == 1
