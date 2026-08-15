"""Ports for versioned Model Policy and Invocation persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pangi.application.contracts.model_persistence import (
    ModelInvocationDenial,
    ModelInvocationFinish,
    ModelInvocationStart,
    ModelPolicySnapshot,
)


class ModelPersistenceError(RuntimeError):
    """Model governance metadata could not be persisted safely."""


class ModelPolicyPersistenceError(ModelPersistenceError):
    """A Model Policy snapshot could not be stored or loaded."""


class ModelInvocationPersistenceError(ModelPersistenceError):
    """A governed Model Invocation lifecycle could not be persisted."""


class ModelPolicySnapshotStore(Protocol):
    async def save_draft(
        self,
        snapshot: ModelPolicySnapshot,
        *,
        at: datetime,
    ) -> None:
        """Append one immutable draft Policy version."""

        ...


class ModelInvocationRecorder(Protocol):
    async def start(self, invocation: ModelInvocationStart) -> None:
        """Commit an allowed Invocation before the Provider request begins."""

        ...

    async def deny(self, invocation: ModelInvocationDenial) -> None:
        """Commit a blocked Invocation without calling a Provider."""

        ...

    async def finish(self, invocation: ModelInvocationFinish) -> None:
        """Finalize one previously started Invocation."""

        ...
