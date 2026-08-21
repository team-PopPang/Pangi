"""Ordered lifecycle composition for local runtime resources."""

from __future__ import annotations

from pangi.application.ports.runtime import RuntimeBackend


class CompositeRuntimeBackend:
    """Start resources in dependency order and close them in reverse order."""

    def __init__(self, resources: tuple[RuntimeBackend, ...]) -> None:
        if not resources:
            raise ValueError("composite runtime requires at least one resource")
        self._resources = resources
        self._started: list[RuntimeBackend] = []

    async def start(self) -> None:
        if self._started:
            return
        try:
            for resource in self._resources:
                await resource.start()
                self._started.append(resource)
        except BaseException:
            await self._close_started(suppress=True)
            raise

    async def close(self) -> None:
        await self._close_started(suppress=False)

    async def _close_started(self, *, suppress: bool) -> None:
        first_error: BaseException | None = None
        while self._started:
            resource = self._started.pop()
            try:
                await resource.close()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None and not suppress:
            raise first_error
