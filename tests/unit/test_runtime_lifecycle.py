"""Ordered local runtime lifecycle tests."""

import asyncio

import pytest

from pangi.application.services.runtime_lifecycle import CompositeRuntimeBackend


class Resource:
    def __init__(self, name: str, events: list[str], *, fail_start: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail_start = fail_start

    async def start(self) -> None:
        self.events.append(f"start:{self.name}")
        if self.fail_start:
            raise RuntimeError("startup failed")

    async def close(self) -> None:
        self.events.append(f"close:{self.name}")


def test_composite_runtime_starts_in_order_and_closes_in_reverse() -> None:
    async def scenario() -> None:
        events: list[str] = []
        runtime = CompositeRuntimeBackend(
            (Resource("sqlite", events), Resource("queue", events))
        )

        await runtime.start()
        await runtime.start()
        await runtime.close()
        await runtime.close()

        assert events == [
            "start:sqlite",
            "start:queue",
            "close:queue",
            "close:sqlite",
        ]

    asyncio.run(scenario())


def test_composite_runtime_closes_started_dependencies_after_start_failure() -> None:
    async def scenario() -> None:
        events: list[str] = []
        runtime = CompositeRuntimeBackend(
            (
                Resource("sqlite", events),
                Resource("queue", events, fail_start=True),
            )
        )

        with pytest.raises(RuntimeError, match="startup failed"):
            await runtime.start()

        assert events == ["start:sqlite", "start:queue", "close:sqlite"]

    asyncio.run(scenario())
