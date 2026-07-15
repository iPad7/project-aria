"""PasswordHasher 포트의 Argon2 구현 (pwdlib)."""

from __future__ import annotations

from pwdlib import PasswordHash


class Argon2PasswordHasher:
    def __init__(self) -> None:
        self._hasher = PasswordHash.recommended()

    def hash(self, raw: str) -> str:
        return self._hasher.hash(raw)

    def verify(self, raw: str, hashed: str) -> bool:
        return self._hasher.verify(raw, hashed)
