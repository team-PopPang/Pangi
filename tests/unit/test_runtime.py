"""Public runtime lifecycle facade tests."""

import asyncio

from pangi import PangiRuntime


class RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start(self) -> None:
        self.calls.append("start")

    async def close(self) -> None:
        self.calls.append("close")


def test_runtime_context_owns_backend_lifecycle() -> None:
    backend = RecordingBackend()

    async def exercise() -> None:
        runtime = PangiRuntime(backend)
        assert runtime.started is False
        async with runtime:
            assert runtime.started is True
            await runtime.start()
        assert runtime.started is False
        await runtime.close()

    asyncio.run(exercise())

    assert backend.calls == ["start", "close"]

