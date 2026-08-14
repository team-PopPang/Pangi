"""Composition root for wiring application ports to concrete adapters."""

from pangi.application.ports.runtime import RuntimeBackend
from pangi.runtime import PangiRuntime


def create_runtime(backend: RuntimeBackend) -> PangiRuntime:
    """Build the public runtime facade around an application backend."""

    return PangiRuntime(backend)

