"""Secret contracts, Keyring adapter, routing, and composition tests."""

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from pangi.adapters.outbound.secrets.factory import build_secret_store
from pangi.adapters.outbound.secrets.keyring import KEYRING_SERVICE, KeyringSecretStore
from pangi.adapters.outbound.secrets.router import RoutedSecretStore
from pangi.application.contracts.secrets import (
    MAX_SECRET_BYTES,
    SecretBackend,
    SecretContractError,
    SecretMaterial,
    SecretReference,
)
from pangi.application.ports.secrets import (
    SecretIntegrityError,
    SecretNotFoundError,
    SecretStoreUnavailableError,
)
from pangi.config import SecretStoreConfig


class FakeKeyring:
    def __init__(self, *, priority: float = 1) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.priority = priority
        self.fail_get = False
        self.fail_set = False
        self.fail_delete = False

    def get_keyring(self) -> object:
        return SimpleNamespace(priority=self.priority)

    def get_password(self, service_name: str, username: str) -> str | None:
        if self.fail_get:
            raise RuntimeError("private keyring failure")
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        if self.fail_set:
            raise RuntimeError(f"private keyring failure: {password}")
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        if self.fail_delete:
            raise RuntimeError("private keyring failure")
        del self.values[(service_name, username)]


class FakeAead:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def encrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        assert associated_data is not None
        return b"authenticated:" + data

    def decrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        assert associated_data is not None
        if not data.startswith(b"authenticated:"):
            raise ValueError
        return data.removeprefix(b"authenticated:")


class FailingStore:
    def __init__(self) -> None:
        self.create_calls = 0

    async def create(self, material: SecretMaterial) -> SecretReference:
        self.create_calls += 1
        raise SecretStoreUnavailableError

    async def get(self, reference: SecretReference) -> SecretMaterial:
        raise SecretStoreUnavailableError

    async def replace(self, reference: SecretReference, material: SecretMaterial) -> None:
        raise SecretStoreUnavailableError

    async def delete(self, reference: SecretReference) -> bool:
        raise SecretStoreUnavailableError


class RecordingStore(FailingStore):
    async def create(self, material: SecretMaterial) -> SecretReference:
        self.create_calls += 1
        return SecretReference(SecretBackend.FILE_VAULT, "recording_identifier")


def test_secret_reference_round_trips_without_revealing_internal_path() -> None:
    reference = SecretReference(SecretBackend.FILE_VAULT, "opaque_identifier_123")

    parsed = SecretReference.parse(reference.value)

    assert parsed == reference
    assert reference.value == "secret:v1:file-vault:opaque_identifier_123"
    assert "opaque_identifier_123" not in repr(reference)
    assert "/" not in reference.value


@pytest.mark.parametrize(
    "value",
    [
        "secret:v2:keyring:opaque_identifier_123",
        "secret:v1:unknown:opaque_identifier_123",
        "secret:v1:file-vault:../escape",
        "private-token",
        None,
    ],
)
def test_invalid_secret_references_are_rejected_without_echoing_input(value: object) -> None:
    with pytest.raises(SecretContractError) as captured:
        SecretReference.parse(value)

    assert str(value) not in str(captured.value)


def test_secret_material_is_bounded_and_hidden_from_representations() -> None:
    secret = "private-token-value"
    material = SecretMaterial(secret)

    assert material.byte_length == len(secret)
    assert secret not in repr(material)
    assert secret not in str(material)

    with pytest.raises(SecretContractError):
        SecretMaterial("")
    with pytest.raises(SecretContractError):
        SecretMaterial("x" * (MAX_SECRET_BYTES + 1))


def test_keyring_adapter_round_trip_replace_and_idempotent_delete() -> None:
    api = FakeKeyring()
    store = KeyringSecretStore(api, identifier_factory=lambda: "keyring_identifier_123")

    reference = asyncio.run(store.create(SecretMaterial("first-private-value")))
    loaded = asyncio.run(store.get(reference))
    asyncio.run(store.replace(reference, SecretMaterial("second-private-value")))
    replaced = asyncio.run(store.get(reference))

    assert reference.backend is SecretBackend.KEYRING
    assert api.values[(KEYRING_SERVICE, reference.identifier)] == "second-private-value"
    assert loaded.value == "first-private-value"
    assert replaced.value == "second-private-value"
    assert asyncio.run(store.delete(reference))
    assert not asyncio.run(store.delete(reference))


def test_keyring_missing_and_unavailable_are_distinct_and_secret_safe() -> None:
    api = FakeKeyring()
    store = KeyringSecretStore(api)
    missing = SecretReference(SecretBackend.KEYRING, "missing_identifier_123")

    with pytest.raises(SecretNotFoundError) as missing_error:
        asyncio.run(store.get(missing))

    api.values[(KEYRING_SERVICE, missing.identifier)] = "private-token-value"
    api.fail_get = True
    with pytest.raises(SecretStoreUnavailableError) as unavailable_error:
        asyncio.run(store.get(missing))

    assert missing_error.value.code == "secret_not_found"
    assert unavailable_error.value.code == "secret_store_unavailable"
    assert "private-token-value" not in repr(unavailable_error.value)
    assert "private keyring failure" not in repr(unavailable_error.value)


def test_keyring_corrupt_material_is_reported_as_integrity_failure() -> None:
    api = FakeKeyring()
    reference = SecretReference(SecretBackend.KEYRING, "corrupt_identifier_123")
    api.values[(KEYRING_SERVICE, reference.identifier)] = ""

    with pytest.raises(SecretIntegrityError):
        asyncio.run(KeyringSecretStore(api).get(reference))


def test_router_never_falls_back_after_the_primary_operation_starts() -> None:
    primary = FailingStore()
    secondary = RecordingStore()
    router = RoutedSecretStore(
        primary=SecretBackend.KEYRING,
        stores={
            SecretBackend.KEYRING: primary,
            SecretBackend.FILE_VAULT: secondary,
        },
    )

    with pytest.raises(SecretStoreUnavailableError):
        asyncio.run(router.create(SecretMaterial("private-token-value")))

    assert primary.create_calls == 1
    assert secondary.create_calls == 0


def test_router_uses_the_reference_backend_instead_of_the_primary() -> None:
    primary = FailingStore()
    secondary = RecordingStore()
    router = RoutedSecretStore(
        primary=SecretBackend.KEYRING,
        stores={
            SecretBackend.KEYRING: primary,
            SecretBackend.FILE_VAULT: secondary,
        },
    )

    reference = SecretReference(SecretBackend.FILE_VAULT, "recording_identifier")
    with pytest.raises(SecretStoreUnavailableError):
        asyncio.run(router.get(reference))

    assert primary.create_calls == 0


def test_auto_selection_prefers_usable_keyring(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir(mode=0o700)
    keyring = FakeKeyring(priority=1)
    encoded_key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")

    store = build_secret_store(
        SecretStoreConfig(),
        vault_dir=vault,
        environ={"PANGI_SECRET_MASTER_KEY": encoded_key},
        keyring_module=keyring,
        aead_factory=FakeAead,
    )

    assert store.primary_backend is SecretBackend.KEYRING
    reference = asyncio.run(store.create(SecretMaterial("private-token-value")))
    assert reference.backend is SecretBackend.KEYRING


def test_auto_selection_uses_configured_file_vault_when_keyring_is_unavailable(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir(mode=0o700)
    encoded_key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")

    store = build_secret_store(
        SecretStoreConfig(),
        vault_dir=vault,
        environ={"PANGI_SECRET_MASTER_KEY": encoded_key},
        keyring_module=FakeKeyring(priority=0),
        aead_factory=FakeAead,
    )

    assert store.primary_backend is SecretBackend.FILE_VAULT
    reference = asyncio.run(store.create(SecretMaterial("private-token-value")))
    assert reference.backend is SecretBackend.FILE_VAULT


def test_auto_selection_fails_closed_when_no_backend_is_available(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir(mode=0o700)

    with pytest.raises(SecretStoreUnavailableError):
        build_secret_store(
            SecretStoreConfig(),
            vault_dir=vault,
            environ={},
            keyring_module=FakeKeyring(priority=0),
            aead_factory=FakeAead,
        )
