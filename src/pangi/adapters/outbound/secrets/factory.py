"""SecretStore composition with one-time backend selection."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pangi.adapters.outbound.secrets.file_vault import (
    AeadFactory,
    FileVaultSecretStore,
    decode_master_key,
    load_master_key_file,
)
from pangi.adapters.outbound.secrets.keyring import (
    KeyringModule,
    build_keyring_secret_store,
)
from pangi.adapters.outbound.secrets.router import RoutedSecretStore
from pangi.application.contracts.secrets import SecretBackend
from pangi.application.ports.secrets import SecretStore, SecretStoreUnavailableError
from pangi.config import SecretStoreConfig


def _build_file_vault(
    config: SecretStoreConfig,
    *,
    vault_dir: Path,
    environ: Mapping[str, str],
    aead_factory: AeadFactory | None,
) -> FileVaultSecretStore:
    if config.master_key_source == "environment":
        encoded = environ.get(config.master_key_environment_variable)
        if encoded is None:
            raise SecretStoreUnavailableError
        master_key = decode_master_key(encoded)
    else:
        if config.master_key_file is None:
            raise SecretStoreUnavailableError
        master_key = load_master_key_file(
            Path(config.master_key_file),
            vault_dir=vault_dir,
        )
    return FileVaultSecretStore(
        vault_dir,
        master_key,
        aead_factory=aead_factory,
    )


def build_secret_store(
    config: SecretStoreConfig,
    *,
    vault_dir: Path,
    environ: Mapping[str, str] | None = None,
    keyring_module: KeyringModule | None = None,
    aead_factory: AeadFactory | None = None,
) -> RoutedSecretStore:
    """Select a creation backend once and retain deterministic reference routing."""

    environment = os.environ if environ is None else environ
    if config.backend == SecretBackend.KEYRING.value:
        keyring = build_keyring_secret_store(module=keyring_module)
        return RoutedSecretStore(
            primary=SecretBackend.KEYRING,
            stores={SecretBackend.KEYRING: keyring},
        )
    if config.backend == SecretBackend.FILE_VAULT.value:
        file_vault = _build_file_vault(
            config,
            vault_dir=vault_dir,
            environ=environment,
            aead_factory=aead_factory,
        )
        return RoutedSecretStore(
            primary=SecretBackend.FILE_VAULT,
            stores={SecretBackend.FILE_VAULT: file_vault},
        )

    stores: dict[SecretBackend, SecretStore] = {}
    try:
        stores[SecretBackend.KEYRING] = build_keyring_secret_store(module=keyring_module)
    except SecretStoreUnavailableError:
        pass
    try:
        stores[SecretBackend.FILE_VAULT] = _build_file_vault(
            config,
            vault_dir=vault_dir,
            environ=environment,
            aead_factory=aead_factory,
        )
    except SecretStoreUnavailableError:
        pass

    if SecretBackend.KEYRING in stores:
        primary = SecretBackend.KEYRING
    elif SecretBackend.FILE_VAULT in stores:
        primary = SecretBackend.FILE_VAULT
    else:
        raise SecretStoreUnavailableError
    return RoutedSecretStore(primary=primary, stores=stores)
