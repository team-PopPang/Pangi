"""Explicit unavailable runtime used until persistence and Web adapters land."""

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

