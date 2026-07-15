"""아웃바운드 포트: 비밀번호 해싱. 도메인이 해싱 알고리즘을 모르게 격리한다."""

from __future__ import annotations

from typing import Protocol


class PasswordHasher(Protocol):
    def hash(self, raw: str) -> str: ...

    def verify(self, raw: str, hashed: str) -> bool: ...
