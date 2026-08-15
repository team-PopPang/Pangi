"""Ports used by the framework-free input-guardrail application service."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.guardrails import ExplicitSkillAccess
from pangi.application.contracts.runs import RunCreation
from pangi.domain.runs import RunRequest


class ExplicitSkillAuthorizer(Protocol):
    async def check_access(
        self,
        *,
        actor: AuthenticatedPrincipal,
        explicit_skill: str,
    ) -> ExplicitSkillAccess:
        """Return access without interpreting or rewriting the skill identifier."""

        ...


class InputRateLimiter(Protocol):
    def reserve(
        self,
        key: str,
        *,
        at: datetime,
        limit: int,
        window_seconds: int,
    ) -> int | None:
        """Reserve one request or return a secret-safe retry delay in seconds."""

        ...


class RunCreator(Protocol):
    async def create_run(self, request: RunRequest, *, route_key: str) -> RunCreation:
        """Persist one admitted Run or replay its existing idempotent result."""

        ...
