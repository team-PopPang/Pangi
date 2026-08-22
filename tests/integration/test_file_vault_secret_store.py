"""Encrypted File Vault filesystem and cryptographic integration tests."""

import asyncio
import base64
import json
import stat
from pathlib import Path

import pytest

from pangi.adapters.outbound.secrets import file_vault as file_vault_adapter
from pangi.adapters.outbound.secrets.file_vault import (
    FileVaultSecretStore,
    load_master_key_file,
)
from pangi.application.contracts.secrets import SecretMaterial
from pangi.application.ports.secrets import (
    SecretIntegrityError,
    SecretNotFoundError,
    SecretStoreUnavailableError,
)


def _vault(tmp_path: Path, *, key: bytes = b"k" * 32) -> FileVaultSecretStore:
    pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(mode=0o700)
    return FileVaultSecretStore(vault_dir, key)


def _envelope_path(tmp_path: Path, identifier: str) -> Path:
    return tmp_path / "vault" / f"{identifier}.secret.json"


def test_file_vault_round_trip_replace_delete_and_permissions(tmp_path: Path) -> None:
    store = _vault(tmp_path)
    first = "first-private-token"
    second = "second-private-token"

    reference = asyncio.run(store.create(SecretMaterial(first)))
    path = _envelope_path(tmp_path, reference.identifier)
    before = json.loads(path.read_text("ascii"))
    asyncio.run(store.replace(reference, SecretMaterial(second)))
    after = json.loads(path.read_text("ascii"))

    assert asyncio.run(store.get(reference)).value == second
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert first not in path.read_text("ascii")
    assert second not in path.read_text("ascii")
    assert before["nonce"] != after["nonce"]
    assert before["secret_ref"] == reference.value
    assert after["secret_ref"] == reference.value
    assert asyncio.run(store.delete(reference))
    assert not asyncio.run(store.delete(reference))
    with pytest.raises(SecretNotFoundError):
        asyncio.run(store.get(reference))


def test_file_vault_rejects_ciphertext_tampering_and_wrong_master_key(tmp_path: Path) -> None:
    store = _vault(tmp_path)
    reference = asyncio.run(store.create(SecretMaterial("private-token-value")))
    path = _envelope_path(tmp_path, reference.identifier)
    envelope = json.loads(path.read_text("ascii"))
    ciphertext = bytearray(base64.urlsafe_b64decode(envelope["ciphertext"]))
    ciphertext[-1] ^= 1
    envelope["ciphertext"] = base64.urlsafe_b64encode(ciphertext).decode("ascii")
    path.write_text(
        json.dumps(envelope, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        "ascii",
    )
    path.chmod(0o600)

    with pytest.raises(SecretIntegrityError):
        asyncio.run(store.get(reference))

    path.unlink()
    reference = asyncio.run(store.create(SecretMaterial("another-private-value")))
    wrong_key_store = FileVaultSecretStore(tmp_path / "vault", b"z" * 32)
    with pytest.raises(SecretIntegrityError):
        asyncio.run(wrong_key_store.get(reference))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("key_version", 2),
        ("backend", "keyring"),
        ("secret_ref", "secret:v1:file-vault:different_identifier"),
    ],
)
def test_file_vault_rejects_authenticated_metadata_changes(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    store = _vault(tmp_path)
    reference = asyncio.run(store.create(SecretMaterial("private-token-value")))
    path = _envelope_path(tmp_path, reference.identifier)
    envelope = json.loads(path.read_text("ascii"))
    envelope[field] = value
    path.write_text(
        json.dumps(envelope, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        "ascii",
    )
    path.chmod(0o600)

    with pytest.raises(SecretIntegrityError):
        asyncio.run(store.get(reference))


def test_file_vault_rejects_unsafe_directory_file_and_symlink(tmp_path: Path) -> None:
    pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")
    unsafe_vault = tmp_path / "unsafe-vault"
    unsafe_vault.mkdir(mode=0o755)
    unsafe_vault.chmod(0o755)
    with pytest.raises(SecretStoreUnavailableError):
        FileVaultSecretStore(unsafe_vault, b"k" * 32)

    real_vault = tmp_path / "real-vault"
    real_vault.mkdir(mode=0o700)
    linked_vault = tmp_path / "linked-vault"
    linked_vault.symlink_to(real_vault, target_is_directory=True)
    with pytest.raises(SecretStoreUnavailableError):
        FileVaultSecretStore(linked_vault, b"k" * 32)

    store = _vault(tmp_path)
    reference = asyncio.run(store.create(SecretMaterial("private-token-value")))
    path = _envelope_path(tmp_path, reference.identifier)
    target = tmp_path / "moved-envelope"
    path.rename(target)
    path.symlink_to(target)

    with pytest.raises(SecretIntegrityError):
        asyncio.run(store.get(reference))


def test_failed_atomic_replace_preserves_the_previous_ciphertext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _vault(tmp_path)
    reference = asyncio.run(store.create(SecretMaterial("first-private-token")))
    path = _envelope_path(tmp_path, reference.identifier)
    previous = path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("private filesystem detail")

    monkeypatch.setattr(file_vault_adapter.os, "replace", fail_replace)

    with pytest.raises(SecretStoreUnavailableError) as captured:
        asyncio.run(store.replace(reference, SecretMaterial("second-private-token")))

    assert path.read_bytes() == previous
    assert "private filesystem detail" not in repr(captured.value)
    assert not tuple((tmp_path / "vault").glob("*.tmp"))


def test_file_vault_rejects_directory_replacement_after_selection(tmp_path: Path) -> None:
    store = _vault(tmp_path)
    reference = asyncio.run(store.create(SecretMaterial("private-token-value")))
    original_vault = tmp_path / "vault"
    moved_vault = tmp_path / "moved-vault"
    original_vault.rename(moved_vault)
    original_vault.mkdir(mode=0o700)

    with pytest.raises(SecretStoreUnavailableError):
        asyncio.run(store.get(reference))


def test_concurrent_replacements_leave_one_valid_envelope(tmp_path: Path) -> None:
    store = _vault(tmp_path)
    reference = asyncio.run(store.create(SecretMaterial("initial-private-token")))

    async def replace_all() -> None:
        await asyncio.gather(
            *(
                store.replace(reference, SecretMaterial(f"private-token-{index}"))
                for index in range(8)
            )
        )

    asyncio.run(replace_all())

    assert asyncio.run(store.get(reference)).value in {
        f"private-token-{index}" for index in range(8)
    }


def test_master_key_file_must_be_external_owner_only_and_regular(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir(mode=0o700)
    encoded = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
    key_file = tmp_path / "master.key"
    key_file.write_text(encoded + "\n", "ascii")
    key_file.chmod(0o600)

    assert load_master_key_file(key_file, vault_dir=vault) == b"k" * 32

    key_file.chmod(0o644)
    with pytest.raises(SecretStoreUnavailableError):
        load_master_key_file(key_file, vault_dir=vault)

    key_file.chmod(0o600)
    symlink = tmp_path / "master-link.key"
    symlink.symlink_to(key_file)
    with pytest.raises(SecretStoreUnavailableError):
        load_master_key_file(symlink, vault_dir=vault)

    linked_parent = tmp_path / "linked-parent"
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    parent_key = real_parent / "master.key"
    parent_key.write_text(encoded, "ascii")
    parent_key.chmod(0o600)
    with pytest.raises(SecretStoreUnavailableError):
        load_master_key_file(linked_parent / "master.key", vault_dir=vault)

    inside_vault = vault / "master.key"
    inside_vault.write_text(encoded, "ascii")
    inside_vault.chmod(0o600)
    with pytest.raises(SecretStoreUnavailableError):
        load_master_key_file(inside_vault, vault_dir=vault)


def test_vault_envelope_never_contains_plaintext_or_master_key(tmp_path: Path) -> None:
    master_key = bytes(range(32))
    store = _vault(tmp_path, key=master_key)
    plaintext = "sk-private-credential-value"
    reference = asyncio.run(store.create(SecretMaterial(plaintext)))

    persisted = _envelope_path(tmp_path, reference.identifier).read_bytes()

    assert plaintext.encode() not in persisted
    assert master_key not in persisted
    assert not any(plaintext in str(value) for value in (store, reference))
