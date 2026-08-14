"""In-process serialization for the single-writer SQLite profile."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class SqliteWriteCoordinator:
    """Serialize write transactions before SQLite lock acquisition."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def serialized(self) -> AsyncIterator[None]:
        async with self._lock:
            yield
