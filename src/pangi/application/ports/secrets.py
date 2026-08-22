"""SecretStore port and stable secret-safe failures."""

from __future__ import annotations

from typing import Protocol

from pangi.application.contracts.secrets import SecretMaterial, SecretReference


class SecretStoreError(RuntimeError):
    """Base class for failures that never include a Secret or backend exception."""

    code = "secret_store_operation_failed"
    safe_message = "Secret Store operation failed"

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class SecretNotFoundError(SecretStoreError):
    code = "secret_not_found"
    safe_message = "Secret was not found"


class SecretStoreUnavailableError(SecretStoreError):
    code = "secret_store_unavailable"
    safe_message = "Secret Store is unavailable"


class SecretIntegrityError(SecretStoreError):
    code = "secret_integrity_failed"
    safe_message = "Secret integrity validation failed"


class SecretStore(Protocol):
    async def create(self, material: SecretMaterial) -> SecretReference:
        """Store a new Secret and return its opaque reference."""

        ...

    async def get(self, reference: SecretReference) -> SecretMaterial:
        """Return one exact Secret or raise a stable safe error."""

        ...

    async def replace(self, reference: SecretReference, material: SecretMaterial) -> None:
        """Atomically replace an existing Secret without changing its reference."""

        ...

    async def delete(self, reference: SecretReference) -> bool:
        """Delete a Secret and report whether it existed."""

        ...
