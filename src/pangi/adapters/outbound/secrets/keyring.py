"""Operating-system Keyring SecretStore adapter with lazy dependency loading."""

from __future__ import annotations

import asyncio
import importlib
import secrets
from collections.abc import Callable
from typing import Protocol, cast

from pangi.application.contracts.secrets import (
    SecretBackend,
    SecretMaterial,
    SecretReference,
)
from pangi.application.ports.secrets import (
    SecretIntegrityError,
    SecretNotFoundError,
    SecretStoreUnavailableError,
)

KEYRING_SERVICE = "pangi-agent/v1"
_CREATE_ATTEMPTS = 8


class KeyringAPI(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None:
        """Read one password from the selected backend."""

        ...

    def set_password(self, service_name: str, username: str, password: str) -> None:
        """Write one password to the selected backend."""

        ...

    def delete_password(self, service_name: str, username: str) -> None:
        """Delete one password from the selected backend."""

        ...


class KeyringModule(KeyringAPI, Protocol):
    def get_keyring(self) -> object:
        """Return the selected backend descriptor."""

        ...


class KeyringSecretStore:
    """Store Secret plaintext only inside an explicitly selected OS Keyring."""

    def __init__(
        self,
        api: KeyringAPI,
        *,
        identifier_factory: Callable[[], str] | None = None,
    ) -> None:
        self._api = api
        self._identifier_factory = identifier_factory or (lambda: secrets.token_urlsafe(24))

    async def create(self, material: SecretMaterial) -> SecretReference:
        return await asyncio.to_thread(self._create, material)

    async def get(self, reference: SecretReference) -> SecretMaterial:
        return await asyncio.to_thread(self._get, reference)

    async def replace(self, reference: SecretReference, material: SecretMaterial) -> None:
        await asyncio.to_thread(self._replace, reference, material)

    async def delete(self, reference: SecretReference) -> bool:
        return await asyncio.to_thread(self._delete, reference)

    @staticmethod
    def _validate_backend(reference: SecretReference) -> None:
        if reference.backend is not SecretBackend.KEYRING:
            raise SecretStoreUnavailableError

    def _read(self, reference: SecretReference) -> str | None:
        try:
            return self._api.get_password(KEYRING_SERVICE, reference.identifier)
        except Exception:
            raise SecretStoreUnavailableError from None

    def _create(self, material: SecretMaterial) -> SecretReference:
        for _ in range(_CREATE_ATTEMPTS):
            try:
                reference = SecretReference(
                    backend=SecretBackend.KEYRING,
                    identifier=self._identifier_factory(),
                )
            except ValueError:
                raise SecretStoreUnavailableError from None
            if self._read(reference) is not None:
                continue
            try:
                self._api.set_password(
                    KEYRING_SERVICE,
                    reference.identifier,
                    material.value,
                )
            except Exception:
                raise SecretStoreUnavailableError from None
            return reference
        raise SecretStoreUnavailableError

    def _get(self, reference: SecretReference) -> SecretMaterial:
        self._validate_backend(reference)
        value = self._read(reference)
        if value is None:
            raise SecretNotFoundError
        try:
            return SecretMaterial(value)
        except ValueError:
            raise SecretIntegrityError from None

    def _replace(self, reference: SecretReference, material: SecretMaterial) -> None:
        self._validate_backend(reference)
        if self._read(reference) is None:
            raise SecretNotFoundError
        try:
            self._api.set_password(
                KEYRING_SERVICE,
                reference.identifier,
                material.value,
            )
        except Exception:
            raise SecretStoreUnavailableError from None

    def _delete(self, reference: SecretReference) -> bool:
        self._validate_backend(reference)
        if self._read(reference) is None:
            return False
        try:
            self._api.delete_password(KEYRING_SERVICE, reference.identifier)
        except Exception:
            raise SecretStoreUnavailableError from None
        return True


def build_keyring_secret_store(
    *,
    module: KeyringModule | None = None,
    identifier_factory: Callable[[], str] | None = None,
) -> KeyringSecretStore:
    """Load and validate the optional Keyring backend only when selected."""

    selected = module
    if selected is None:
        try:
            selected = cast(KeyringModule, importlib.import_module("keyring"))
        except (ImportError, ModuleNotFoundError):
            raise SecretStoreUnavailableError from None
    try:
        backend = selected.get_keyring()
        priority = getattr(backend, "priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, (int, float)) or priority <= 0:
            raise SecretStoreUnavailableError
    except SecretStoreUnavailableError:
        raise
    except Exception:
        raise SecretStoreUnavailableError from None
    return KeyringSecretStore(selected, identifier_factory=identifier_factory)
