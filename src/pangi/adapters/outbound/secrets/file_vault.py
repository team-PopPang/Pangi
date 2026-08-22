"""AES-256-GCM encrypted File Vault with fail-closed filesystem checks."""

from __future__ import annotations

import asyncio
import base64
import binascii
import importlib
import json
import os
import secrets
import stat
import threading
from collections.abc import Callable
from pathlib import Path
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

VAULT_SCHEMA_VERSION = 1
VAULT_KEY_VERSION = 1
AES_256_KEY_BYTES = 32
AES_GCM_NONCE_BYTES = 12
MAX_ENVELOPE_BYTES = 100_000
_CREATE_ATTEMPTS = 8
_ENVELOPE_FIELDS = frozenset(
    {
        "backend",
        "ciphertext",
        "key_version",
        "nonce",
        "schema_version",
        "secret_ref",
    }
)


class AeadCipher(Protocol):
    def encrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        """Encrypt and authenticate one plaintext."""

        ...

    def decrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        """Authenticate and decrypt one ciphertext."""

        ...


class AeadFactory(Protocol):
    def __call__(self, key: bytes) -> AeadCipher:
        """Construct one cipher without retaining plaintext outside the adapter."""

        ...


def decode_master_key(encoded: str) -> bytes:
    """Decode one URL-safe Base64 AES-256 key without exposing invalid input."""

    if not isinstance(encoded, str):
        raise SecretStoreUnavailableError
    normalized = encoded.strip()
    if not normalized or len(normalized) > 256:
        raise SecretStoreUnavailableError
    try:
        key = base64.b64decode(normalized, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error, UnicodeEncodeError):
        raise SecretStoreUnavailableError from None
    if len(key) != AES_256_KEY_BYTES:
        raise SecretStoreUnavailableError
    return key


def load_master_key_file(path: Path, *, vault_dir: Path) -> bytes:
    """Read an external owner-only regular file containing a Base64 master key."""

    if not path.is_absolute():
        raise SecretStoreUnavailableError
    absolute_path = _absolute_without_symlinks(path)
    absolute_vault = _absolute_without_symlinks(vault_dir)
    if absolute_path == absolute_vault or absolute_vault in absolute_path.parents:
        raise SecretStoreUnavailableError
    try:
        metadata = os.lstat(absolute_path)
    except OSError:
        raise SecretStoreUnavailableError from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SecretStoreUnavailableError
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SecretStoreUnavailableError
    _validate_owner(metadata)
    if not 1 <= metadata.st_size <= 256:
        raise SecretStoreUnavailableError

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute_path, flags)
    except OSError:
        raise SecretStoreUnavailableError from None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_size > 256
        ):
            raise SecretStoreUnavailableError
        _validate_owner(opened)
        with os.fdopen(descriptor, "r", encoding="ascii", closefd=False) as key_file:
            encoded = key_file.read(257)
    except (OSError, UnicodeError):
        raise SecretStoreUnavailableError from None
    finally:
        os.close(descriptor)
    return decode_master_key(encoded)


def _load_aead_factory() -> AeadFactory:
    try:
        module = importlib.import_module("cryptography.hazmat.primitives.ciphers.aead")
        return cast(AeadFactory, module.AESGCM)
    except (AttributeError, ImportError, ModuleNotFoundError):
        raise SecretStoreUnavailableError from None


def _validate_owner(metadata: os.stat_result) -> None:
    getuid = getattr(os, "getuid", None)
    if getuid is not None and metadata.st_uid != getuid():
        raise SecretStoreUnavailableError


def _absolute_without_symlinks(path: Path) -> Path:
    absolute = path.absolute()
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise SecretStoreUnavailableError from None
    if resolved != absolute:
        raise SecretStoreUnavailableError
    return absolute


def _encode_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode_base64(value: object) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_ENVELOPE_BYTES:
        raise SecretIntegrityError
    try:
        return base64.b64decode(value, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error, UnicodeEncodeError):
        raise SecretIntegrityError from None


def _associated_data(reference: SecretReference) -> bytes:
    return json.dumps(
        {
            "backend": SecretBackend.FILE_VAULT.value,
            "key_version": VAULT_KEY_VERSION,
            "schema_version": VAULT_SCHEMA_VERSION,
            "secret_ref": reference.value,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


class FileVaultSecretStore:
    """Encrypt each Secret into one owner-only atomically published envelope."""

    def __init__(
        self,
        vault_dir: Path,
        master_key: bytes,
        *,
        aead_factory: AeadFactory | None = None,
        identifier_factory: Callable[[], str] | None = None,
        nonce_factory: Callable[[int], bytes] | None = None,
    ) -> None:
        if not isinstance(master_key, bytes) or len(master_key) != AES_256_KEY_BYTES:
            raise SecretStoreUnavailableError
        self._vault_dir = _absolute_without_symlinks(vault_dir)
        self._master_key = master_key
        self._aead_factory = aead_factory or _load_aead_factory()
        self._identifier_factory = identifier_factory or (lambda: secrets.token_urlsafe(24))
        self._nonce_factory = nonce_factory or secrets.token_bytes
        self._lock = threading.RLock()
        metadata = self._vault_metadata()
        self._vault_identity = (metadata.st_dev, metadata.st_ino)

    async def create(self, material: SecretMaterial) -> SecretReference:
        return await asyncio.to_thread(self._create, material)

    async def get(self, reference: SecretReference) -> SecretMaterial:
        return await asyncio.to_thread(self._get, reference)

    async def replace(self, reference: SecretReference, material: SecretMaterial) -> None:
        await asyncio.to_thread(self._replace, reference, material)

    async def delete(self, reference: SecretReference) -> bool:
        return await asyncio.to_thread(self._delete, reference)

    def _vault_metadata(self) -> os.stat_result:
        _absolute_without_symlinks(self._vault_dir)
        try:
            metadata = os.lstat(self._vault_dir)
        except OSError:
            raise SecretStoreUnavailableError from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SecretStoreUnavailableError
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise SecretStoreUnavailableError
        _validate_owner(metadata)
        return metadata

    def _validate_vault_directory(self) -> None:
        metadata = self._vault_metadata()
        if (metadata.st_dev, metadata.st_ino) != self._vault_identity:
            raise SecretStoreUnavailableError

    @staticmethod
    def _validate_backend(reference: SecretReference) -> None:
        if reference.backend is not SecretBackend.FILE_VAULT:
            raise SecretStoreUnavailableError

    def _path(self, reference: SecretReference) -> Path:
        return self._vault_dir / f"{reference.identifier}.secret.json"

    def _target_metadata(self, path: Path) -> os.stat_result | None:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError:
            raise SecretStoreUnavailableError from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SecretIntegrityError
        if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
            raise SecretIntegrityError
        try:
            _validate_owner(metadata)
        except SecretStoreUnavailableError:
            raise SecretIntegrityError from None
        if not 1 <= metadata.st_size <= MAX_ENVELOPE_BYTES:
            raise SecretIntegrityError
        return metadata

    def _cipher(self) -> AeadCipher:
        try:
            return self._aead_factory(self._master_key)
        except Exception:
            raise SecretStoreUnavailableError from None

    def _encrypt(self, reference: SecretReference, material: SecretMaterial) -> bytes:
        nonce = self._nonce_factory(AES_GCM_NONCE_BYTES)
        if not isinstance(nonce, bytes) or len(nonce) != AES_GCM_NONCE_BYTES:
            raise SecretStoreUnavailableError
        try:
            ciphertext = self._cipher().encrypt(
                nonce,
                material.value.encode("utf-8"),
                _associated_data(reference),
            )
        except SecretStoreUnavailableError:
            raise
        except Exception:
            raise SecretStoreUnavailableError from None
        envelope = {
            "backend": SecretBackend.FILE_VAULT.value,
            "ciphertext": _encode_base64(ciphertext),
            "key_version": VAULT_KEY_VERSION,
            "nonce": _encode_base64(nonce),
            "schema_version": VAULT_SCHEMA_VERSION,
            "secret_ref": reference.value,
        }
        encoded = json.dumps(
            envelope,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        if len(encoded) > MAX_ENVELOPE_BYTES:
            raise SecretStoreUnavailableError
        return encoded

    def _decrypt(self, reference: SecretReference, encoded: bytes) -> SecretMaterial:
        try:
            envelope = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SecretIntegrityError from None
        if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_FIELDS:
            raise SecretIntegrityError
        if (
            envelope.get("backend") != SecretBackend.FILE_VAULT.value
            or envelope.get("schema_version") != VAULT_SCHEMA_VERSION
            or envelope.get("key_version") != VAULT_KEY_VERSION
            or envelope.get("secret_ref") != reference.value
        ):
            raise SecretIntegrityError
        nonce = _decode_base64(envelope.get("nonce"))
        ciphertext = _decode_base64(envelope.get("ciphertext"))
        if len(nonce) != AES_GCM_NONCE_BYTES or len(ciphertext) < 16:
            raise SecretIntegrityError
        try:
            plaintext = self._cipher().decrypt(
                nonce,
                ciphertext,
                _associated_data(reference),
            )
            value = plaintext.decode("utf-8")
            return SecretMaterial(value)
        except SecretStoreUnavailableError:
            raise
        except Exception:
            raise SecretIntegrityError from None

    def _write_temp(self, reference: SecretReference, encoded: bytes) -> Path:
        temporary = self._vault_dir / (
            f".{reference.identifier}.{secrets.token_hex(12)}.tmp"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(temporary, flags, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb", closefd=False) as output:
                    output.write(encoded)
                    output.flush()
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise SecretStoreUnavailableError from None
        return temporary

    def _sync_directory(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(self._vault_dir, flags)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            raise SecretStoreUnavailableError from None

    def _publish_new(self, path: Path, temporary: Path) -> bool:
        try:
            os.link(temporary, path, follow_symlinks=False)
            temporary.unlink()
            self._sync_directory()
            return True
        except FileExistsError:
            return False
        except OSError:
            raise SecretStoreUnavailableError from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _publish_replacement(self, path: Path, temporary: Path) -> None:
        try:
            os.replace(temporary, path)
            self._sync_directory()
        except OSError:
            raise SecretStoreUnavailableError from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _read(self, reference: SecretReference) -> bytes:
        path = self._path(reference)
        metadata = self._target_metadata(path)
        if metadata is None:
            raise SecretNotFoundError
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            raise SecretNotFoundError from None
        except OSError:
            raise SecretStoreUnavailableError from None
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_nlink != 1
                or opened.st_dev != metadata.st_dev
                or opened.st_ino != metadata.st_ino
                or not 1 <= opened.st_size <= MAX_ENVELOPE_BYTES
            ):
                raise SecretIntegrityError
            try:
                _validate_owner(opened)
            except SecretStoreUnavailableError:
                raise SecretIntegrityError from None
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                encoded = source.read(MAX_ENVELOPE_BYTES + 1)
        except OSError:
            raise SecretStoreUnavailableError from None
        finally:
            os.close(descriptor)
        if not 1 <= len(encoded) <= MAX_ENVELOPE_BYTES:
            raise SecretIntegrityError
        return encoded

    def _create(self, material: SecretMaterial) -> SecretReference:
        with self._lock:
            self._validate_vault_directory()
            for _ in range(_CREATE_ATTEMPTS):
                try:
                    reference = SecretReference(
                        backend=SecretBackend.FILE_VAULT,
                        identifier=self._identifier_factory(),
                    )
                except ValueError:
                    raise SecretStoreUnavailableError from None
                path = self._path(reference)
                try:
                    if os.lstat(path):
                        continue
                except FileNotFoundError:
                    pass
                except OSError:
                    raise SecretStoreUnavailableError from None
                encoded = self._encrypt(reference, material)
                temporary = self._write_temp(reference, encoded)
                if self._publish_new(path, temporary):
                    return reference
            raise SecretStoreUnavailableError

    def _get(self, reference: SecretReference) -> SecretMaterial:
        self._validate_backend(reference)
        with self._lock:
            self._validate_vault_directory()
            return self._decrypt(reference, self._read(reference))

    def _replace(self, reference: SecretReference, material: SecretMaterial) -> None:
        self._validate_backend(reference)
        with self._lock:
            self._validate_vault_directory()
            path = self._path(reference)
            if self._target_metadata(path) is None:
                raise SecretNotFoundError
            temporary = self._write_temp(reference, self._encrypt(reference, material))
            self._publish_replacement(path, temporary)

    def _delete(self, reference: SecretReference) -> bool:
        self._validate_backend(reference)
        with self._lock:
            self._validate_vault_directory()
            path = self._path(reference)
            if self._target_metadata(path) is None:
                return False
            try:
                path.unlink()
                self._sync_directory()
            except OSError:
                raise SecretStoreUnavailableError from None
            return True
