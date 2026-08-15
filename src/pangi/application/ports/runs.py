"""Run use-case and persistence ports owned by the application layer."""

from __future__ import annotations

from typing import Protocol

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.runs import (
    RunCreateRecord,
    RunCreation,
    RunListPage,
    RunListQuery,
    RunStoreQuery,
    RunSummary,
)
from pangi.domain.runs import Run, RunRequest


class RunOperationError(RuntimeError):
    """Base class for expected, secret-safe Run operation failures."""

    code = "run_operation_failed"


class IdempotencyConflictError(RunOperationError):
    """The same idempotency scope was reused for a different request."""

    code = "idempotency_conflict"


class IdempotencyUnavailableError(RunOperationError):
    """A persisted idempotency record cannot currently be replayed."""

    code = "idempotency_unavailable"


class InvalidRunCursorError(RunOperationError):
    """A cursor is malformed or belongs to another query scope."""

    code = "invalid_run_cursor"


class RunNotFoundError(RunOperationError):
    """A Run is missing or outside the caller's owner scope."""

    code = "run_not_found"


class RunPersistenceError(RunOperationError):
    """Persisted Run data is unavailable or violates its contract."""

    code = "run_persistence_error"


class RunPrincipalUnavailableError(RunOperationError):
    """The request Principal is missing, disabled, or has a stale role."""

    code = "run_principal_unavailable"


class RunRequestConflictError(RunOperationError):
    """A request identifier was already used outside an idempotent replay."""

    code = "run_request_conflict"


class RunOperations(Protocol):
    async def create_run(self, request: RunRequest, *, route_key: str) -> RunCreation:
        """Create one Run or replay the exact existing idempotent result."""

        ...

    async def get_run(self, *, actor: AuthenticatedPrincipal, run_id: str) -> Run:
        """Return one owner-visible Run without leaking foreign existence."""

        ...

    async def list_runs(
        self,
        *,
        actor: AuthenticatedPrincipal,
        query: RunListQuery,
    ) -> RunListPage:
        """Return a stable metadata-only page within the actor's owner scope."""

        ...


class RunStore(Protocol):
    async def create_or_replay(self, record: RunCreateRecord) -> RunCreation:
        """Atomically persist or replay a Run creation record."""

        ...

    async def get_run(self, *, run_id: str, owner_user_id: str | None) -> Run | None:
        """Return a Run inside the effective owner scope."""

        ...

    async def list_run_summaries(self, query: RunStoreQuery) -> tuple[RunSummary, ...]:
        """Return at most query.limit summaries ordered by the stable keyset."""

        ...
