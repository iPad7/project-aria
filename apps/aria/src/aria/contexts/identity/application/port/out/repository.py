"""아웃바운드 포트: 사용자 영속성. 구현은 adapter가 제공한다."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aria.contexts.identity.domain.model import User


class UserRepository(Protocol):
    def add(self, user: User) -> None: ...

    def get_by_email(self, email: str) -> User | None: ...

    def get_by_id(self, user_id: UUID) -> User | None: ...
