"""Runtime lifecycle port."""

from typing import Protocol


class RuntimeBackend(Protocol):
    """Minimal lifecycle contract implemented by a composed runtime backend."""

    async def start(self) -> None:
        """Acquire runtime resources."""

        ...

    async def close(self) -> None:
        """Release runtime resources."""

        ...

