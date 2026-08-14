"""Foreground RuntimeControl status and CLI process integration tests."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from pangi import PangiConfig
from pangi.adapters.outbound.initialization import FileSystemInitializer
from pangi.adapters.outbound.runtime_control import UvicornRuntimeControl
from pangi.adapters.outbound.runtime_paths import resolve_runtime_paths
from pangi.application.contracts.runtime_status import RuntimeState
from pangi.config import ServerConfig


class _JsonHandler(BaseHTTPRequestHandler):
    payload: object = {
        "schema_version": 1,
        "product": "pangi",
        "status": "live",
    }

    def do_GET(self) -> None:
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


async def _unused_app(*_args: object) -> None:
    return


def test_status_requires_a_pangi_live_identity() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        control = UvicornRuntimeControl(
            app=_unused_app,
            host="127.0.0.1",
            port=server.server_port,
        )
        assert control.status().state is RuntimeState.RUNNING

        _JsonHandler.payload = {"status": "live", "product": "another-service"}
        assert control.status().state is RuntimeState.STOPPED
    finally:
        _JsonHandler.payload = {
            "schema_version": 1,
            "product": "pangi",
            "status": "live",
        }
        server.shutdown()
        server.server_close()
        thread.join()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_cli_start_and_status_use_the_composed_runtime(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime"
    paths = resolve_runtime_paths(
        explicit_home=runtime_home,
        environ={},
        platform="linux",
        user_home=tmp_path,
    )
    config = PangiConfig(server=ServerConfig(port=_free_port()))
    initializer = FileSystemInitializer()
    initializer.apply(initializer.plan(paths), config.to_toml())
    environment = {**os.environ, "PANGI_HOME": str(runtime_home)}
    process = subprocess.Popen(
        [sys.executable, "-m", "pangi", "start"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        running: subprocess.CompletedProcess[str] | None = None
        while time.monotonic() < deadline:
            running = subprocess.run(
                [sys.executable, "-m", "pangi", "status", "--json"],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            if running.returncode == 0:
                break
            if process.poll() is not None:
                break
            time.sleep(0.1)

        assert process.poll() is None
        assert running is not None
        assert running.returncode == 0, running.stderr
        assert json.loads(running.stdout)["state"] == "running"

        duplicate = subprocess.run(
            [sys.executable, "-m", "pangi", "start"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert duplicate.returncode == 1
        assert "runtime could not start" in duplicate.stderr
        assert "Traceback" not in duplicate.stderr
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)

    stopped = subprocess.run(
        [sys.executable, "-m", "pangi", "status", "--json"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert stopped.returncode == 1
    assert json.loads(stopped.stdout)["state"] == "stopped"
