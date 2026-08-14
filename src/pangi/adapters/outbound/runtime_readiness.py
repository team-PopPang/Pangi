"""Readiness checks for the composed local runtime."""

from importlib import resources

from pangi.adapters.outbound.persistence.sqlite.database import SqliteDatabase
from pangi.application.contracts.readiness import (
    ReadinessCheckResult,
    ReadinessReport,
    ReadinessState,
)


class LocalRuntimeReadinessProbe:
    """Report only facts required to serve the current local Web runtime."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def report(self) -> ReadinessReport:
        sqlite_ready = self._database.started
        static_root = resources.files("pangi.web").joinpath("static")
        assets_ready = (
            static_root.joinpath("index.html").is_file()
            and static_root.joinpath("asset-manifest.json").is_file()
        )
        return ReadinessReport(
            checks=(
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
            )
        )
