"""Secret-safe contracts independent from storage technologies."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

MAX_SECRET_BYTES = 65_536

_SECRET_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SECRET_REFERENCE = re.compile(
    r"^secret:v1:(?P<backend>keyring|file-vault):(?P<identifier>[A-Za-z0-9_-]{16,128})$"
)


class SecretContractError(ValueError):
    """A Secret value or reference violates the stable internal contract."""


class SecretBackend(StrEnum):
    KEYRING = "keyring"
    FILE_VAULT = "file-vault"


@dataclass(frozen=True, slots=True)
class SecretReference:
    """Versioned opaque reference that reveals no storage path or Secret value."""

    backend: SecretBackend
    identifier: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "backend", SecretBackend(self.backend))
        except ValueError as error:
            raise SecretContractError("Secret reference contains an unsupported backend") from error
        if not isinstance(self.identifier, str) or _SECRET_IDENTIFIER.fullmatch(
            self.identifier
        ) is None:
            raise SecretContractError("Secret reference contains an invalid opaque identifier")

    @property
    def value(self) -> str:
        return f"secret:v1:{self.backend.value}:{self.identifier}"

    @classmethod
    def parse(cls, value: object) -> SecretReference:
        if not isinstance(value, str):
            raise SecretContractError("Secret reference must be text")
        matched = _SECRET_REFERENCE.fullmatch(value)
        if matched is None:
            raise SecretContractError("Secret reference has an invalid format")
        return cls(
            backend=SecretBackend(matched.group("backend")),
            identifier=matched.group("identifier"),
        )


@dataclass(frozen=True, slots=True)
class SecretMaterial:
    """Bounded UTF-8 Secret whose plaintext never appears in object representations."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise SecretContractError("Secret material must be text")
        try:
            encoded = self.value.encode("utf-8")
        except UnicodeEncodeError:
            raise SecretContractError("Secret material must be valid UTF-8 text") from None
        if not encoded:
            raise SecretContractError("Secret material cannot be empty")
        if len(encoded) > MAX_SECRET_BYTES:
            raise SecretContractError("Secret material exceeds the byte limit")

    @property
    def byte_length(self) -> int:
        return len(self.value.encode("utf-8"))
