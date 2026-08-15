"""Ports for Model Profile lookup, Egress Policy lookup, and Provider execution."""

from __future__ import annotations

from typing import Protocol

from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelEgressPolicy,
    ModelProfile,
    ModelProviderResponse,
)


class ModelProfileProvider(Protocol):
    async def list_candidates(self, profile: str) -> tuple[ModelProfile, ...]:
        """Return immutable candidates for one logical Profile."""

        ...


class ModelEgressPolicyProvider(Protocol):
    async def get_policy(self, profile: str) -> ModelEgressPolicy | None:
        """Return one exact policy or None so routing can default-deny."""

        ...


class ModelProvider(Protocol):
    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        """Execute only a request admitted and redacted by the Egress boundary."""

        ...
