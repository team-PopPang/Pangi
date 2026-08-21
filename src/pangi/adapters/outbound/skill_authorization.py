"""Fail-closed explicit Skill authorization before the Skill Registry exists."""

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.guardrails import ExplicitSkillAccess


class UnavailableExplicitSkillAuthorizer:
    async def check_access(
        self,
        *,
        actor: AuthenticatedPrincipal,
        explicit_skill: str,
    ) -> ExplicitSkillAccess:
        del actor, explicit_skill
        return ExplicitSkillAccess.UNAVAILABLE
