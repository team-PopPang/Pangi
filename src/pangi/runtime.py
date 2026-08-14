"""Public runtime facade independent of concrete adapters."""

from types import TracebackType
from typing import Self

from pangi.application.ports.runtime import RuntimeBackend


class PangiRuntime:
    """Own the lifecycle of an application runtime backend.

    Concrete backends are supplied by the composition root. Later work packages
    extend this facade with user-facing run operations without exposing adapters.
    """

    __slots__ = ("_backend", "_started")

    def __init__(self, backend: RuntimeBackend) -> None:
        self._backend = backend
        self._started = False

    @property
    def started(self) -> bool:
        """Return whether the backend lifecycle has started."""

        return self._started

    async def start(self) -> None:
        """Start the backend once."""

        if self._started:
            return
        await self._backend.start()
        self._started = True

    async def close(self) -> None:
        """Close a started backend once."""

        if not self._started:
            return
        await self._backend.close()
        self._started = False

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

