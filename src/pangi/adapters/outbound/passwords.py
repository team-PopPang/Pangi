"""Argon2id local password hashing adapter."""

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.low_level import Type


class Argon2idPasswordHasher:
    def __init__(self) -> None:
        self._hasher = Argon2PasswordHasher(type=Type.ID)

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)
