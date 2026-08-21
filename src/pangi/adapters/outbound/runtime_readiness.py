"""Readiness checks for the composed local runtime."""

from importlib import resources

from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.application.contracts.readiness import (
    ReadinessCheckResult,
    ReadinessReport,
    ReadinessState,
)
from pangi.application.ports.run_queue import RunQueueRuntimeStatus


class LocalRuntimeReadinessProbe:
    """Report only facts required to serve the current local Web runtime."""

    def __init__(
        self,
        database: SqliteDatabase,
        *,
        queue_runtime: RunQueueRuntimeStatus | None = None,
    ) -> None:
        self._database = database
        self._queue_runtime = queue_runtime

    def report(self) -> ReadinessReport:
        sqlite_ready = self._database.started
        static_root = resources.files("pangi.web").joinpath("static")
        assets_ready = (
            static_root.joinpath("index.html").is_file()
            and static_root.joinpath("asset-manifest.json").is_file()
        )
        checks = [
                ReadinessCheckResult(
                    check_id="sqlite.runtime",
                    state=(
                        ReadinessState.READY
                        if sqlite_ready
                        else ReadinessState.NOT_READY
                    ),
                    summary=(
                        "SQLite runtime is available"
                        if sqlite_ready
                        else "SQLite runtime is not available"
                    ),
                ),
                ReadinessCheckResult(
                    check_id="web.assets",
                    state=(
                        ReadinessState.READY
                        if assets_ready
                        else ReadinessState.NOT_READY
                    ),
                    summary=(
                        "Admin assets are available"
                        if assets_ready
                        else "Admin assets are not available"
                    ),
                ),
        ]
        if self._queue_runtime is not None:
            queue_ready = self._queue_runtime.ready
            checks.append(
                ReadinessCheckResult(
                    check_id="run-queue.runtime",
                    state=(
                        ReadinessState.READY
                        if queue_ready
                        else ReadinessState.NOT_READY
                    ),
                    summary=(
                        "Run Queue dispatcher is available"
                        if queue_ready
                        else "Run Queue dispatcher is not available"
                    ),
                )
            )
        return ReadinessReport(checks=tuple(checks))
