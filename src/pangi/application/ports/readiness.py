"""Read-only readiness probe port used by inbound health adapters."""

from typing import Protocol

from pangi.application.contracts.readiness import ReadinessReport


class ReadinessProbe(Protocol):
    def report(self) -> ReadinessReport:
        """Return a secret-safe snapshot without mutating runtime state."""

        ...
