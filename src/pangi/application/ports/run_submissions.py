"""Inbound port for protected local Run submission."""

from __future__ import annotations

from typing import Protocol

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.runs import RunSubmission


class RunSubmissionOperations(Protocol):
    async def submit_run(
        self,
        *,
        actor: AuthenticatedPrincipal,
        text: str,
        idempotency_key: str,
        thread_key: str | None,
        explicit_skill: str | None,
    ) -> RunSubmission:
        """Admit, plan, and enqueue one owner-scoped local Run."""

        ...
