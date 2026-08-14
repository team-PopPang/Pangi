"""Foreground server lifecycle and local status probe adapters."""

from __future__ import annotations

import json
import sys
from collections.abc import Awaitable, Callable
from http.client import HTTPConnection, HTTPException

import uvicorn

from pangi.application.contracts.runtime_status import RuntimeState, RuntimeStatus
from pangi.application.ports.runtime_control import RuntimeUnavailableError


class UnavailableRuntimeControl:
    """Prevent a false-positive start before WBS 03 and WBS 04 are composed."""

    def start(self) -> None:
        raise RuntimeUnavailableError("runtime startup requires WBS 03 and WBS 04")

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(
            state=RuntimeState.UNAVAILABLE,
            detail="runtime startup requires WBS 03 and WBS 04",
        )


AsgiApplication = Callable[..., Awaitable[None]]


class UvicornRuntimeControl:
    """Run one foreground Uvicorn process and probe its product identity."""

    def __init__(
        self,
        *,
        app: AsgiApplication,
        host: str,
        port: int,
        probe_timeout_seconds: float = 1.0,
    ) -> None:
        self._app = app
        self._host = host
        self._port = port
        self._probe_timeout_seconds = probe_timeout_seconds

    def start(self) -> None:
        config = uvicorn.Config(
            app=self._app,
            host=self._host,
            port=self._port,
            workers=1,
            access_log=False,
            log_level="critical",
        )
        server = uvicorn.Server(config)
        print(
            f"Starting Pangi on {self._host}:{self._port}",
            file=sys.stderr,
            flush=True,
        )
        try:
            server.run()
        except SystemExit as error:
            raise RuntimeUnavailableError(
                "runtime could not start on the configured server address"
            ) from error
        if not server.started:
            raise RuntimeUnavailableError("runtime startup did not complete")

    def status(self) -> RuntimeStatus:
        connection = HTTPConnection(
            self._probe_host(),
            self._port,
            timeout=self._probe_timeout_seconds,
        )
        try:
            connection.request(
                "GET",
                "/health/live",
                headers={"Accept": "application/json", "User-Agent": "pangi-status/1"},
            )
            response = connection.getresponse()
            if response.status != 200:
                return self._stopped(f"Pangi live probe returned HTTP {response.status}")
            body = response.read(4097)
            if len(body) > 4096:
                return self._stopped("Pangi live probe response is too large")
            payload: object = json.loads(body)
        except (HTTPException, OSError, ValueError):
            return self._stopped("no Pangi runtime responded at the configured endpoint")
        finally:
            connection.close()

        if not isinstance(payload, dict):
            return self._stopped("configured endpoint is not a Pangi runtime")
        if (
            payload.get("schema_version") != 1
            or payload.get("product") != "pangi"
            or payload.get("status") != "live"
        ):
            return self._stopped("configured endpoint is not a Pangi runtime")
        return RuntimeStatus(
            state=RuntimeState.RUNNING,
            detail="Pangi runtime is responding",
        )

    def _probe_host(self) -> str:
        host = self._host.strip()
        if host == "0.0.0.0":
            return "127.0.0.1"
        if host in {"::", "[::]"}:
            return "::1"
        return host.strip("[]")

    @staticmethod
    def _stopped(detail: str) -> RuntimeStatus:
        return RuntimeStatus(state=RuntimeState.STOPPED, detail=detail)
