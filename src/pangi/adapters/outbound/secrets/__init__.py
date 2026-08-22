"""Secret storage adapters loaded only when explicitly selected."""

from pangi.adapters.outbound.secrets.factory import build_secret_store
from pangi.adapters.outbound.secrets.file_vault import FileVaultSecretStore
from pangi.adapters.outbound.secrets.keyring import KeyringSecretStore
from pangi.adapters.outbound.secrets.router import RoutedSecretStore

__all__ = [
    "FileVaultSecretStore",
    "KeyringSecretStore",
    "RoutedSecretStore",
    "build_secret_store",
]
