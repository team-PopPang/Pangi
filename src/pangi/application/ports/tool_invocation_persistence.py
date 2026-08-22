"""Port for governed Tool Invocation lifecycle persistence."""

from typing import Protocol

from pangi.application.contracts.tool_invocation_persistence import (
    ToolInvocationDenial,
    ToolInvocationFinish,
    ToolInvocationStart,
)


class ToolInvocationPersistenceError(RuntimeError):
    """Tool governance metadata could not be persisted safely."""


class ToolInvocationRecorder(Protocol):
    async def start(self, invocation: ToolInvocationStart) -> None:
        """Commit an allowed attempt before external execution."""

        ...

    async def deny(self, invocation: ToolInvocationDenial) -> None:
        """Commit a blocked attempt without external execution."""

        ...

    async def finish(self, invocation: ToolInvocationFinish) -> None:
        """Finalize one previously started attempt exactly once."""

        ...
