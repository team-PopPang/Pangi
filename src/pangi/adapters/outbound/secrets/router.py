"""Deterministic SecretStore routing without per-operation fallback."""

from __future__ import annotations

from collections.abc import Mapping

from pangi.application.contracts.secrets import (
    SecretBackend,
    SecretMaterial,
    SecretReference,
)
from pangi.application.ports.secrets import SecretStore, SecretStoreUnavailableError


class RoutedSecretStore:
    """Use one primary store for creation and reference-bound stores thereafter."""

    def __init__(
        self,
        *,
        primary: SecretBackend,
        stores: Mapping[SecretBackend, SecretStore],
    ) -> None:
        self._primary = SecretBackend(primary)
        self._stores = dict(stores)
        if self._primary not in self._stores:
            raise SecretStoreUnavailableError

    @property
    def primary_backend(self) -> SecretBackend:
        return self._primary

    def _store(self, backend: SecretBackend) -> SecretStore:
        store = self._stores.get(backend)
        if store is None:
            raise SecretStoreUnavailableError
        return store

    async def create(self, material: SecretMaterial) -> SecretReference:
        return await self._store(self._primary).create(material)

    async def get(self, reference: SecretReference) -> SecretMaterial:
        return await self._store(reference.backend).get(reference)

    async def replace(self, reference: SecretReference, material: SecretMaterial) -> None:
        await self._store(reference.backend).replace(reference, material)

    async def delete(self, reference: SecretReference) -> bool:
        return await self._store(reference.backend).delete(reference)
