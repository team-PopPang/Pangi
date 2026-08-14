"""Runtime lifecycle control port used by the CLI."""

from typing import Protocol

from pangi.application.contracts.runtime_status import RuntimeStatus


class RuntimeUnavailableError(RuntimeError):
    """Raised when later work packages have not composed a runtime yet."""


class RuntimeControl(Protocol):
    def start(self) -> None:
        """Start the composed foreground runtime."""

        ...

    def status(self) -> RuntimeStatus:
        """Return a safe runtime status summary."""

        ...

