"""아웃바운드 포트: 사용자 영속성. 구현은 adapter가 제공한다."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from aria.contexts.identity.domain.model import User


class UserRepository(Protocol):
    def add(self, user: User) -> None: ...

    def get_by_email(self, email: str) -> User | None: ...

    def get_by_id(self, user_id: UUID) -> User | None: ...

    def list_by_ids(self, user_ids: Sequence[UUID]) -> list[User]:
        """여러 사용자를 한 번에. 없는 id는 결과에서 빠지고, 순서는 보장하지 않는다.

        `get_by_id`를 반복하지 않고 별도 메서드를 두는 이유는 N+1 때문이다 —
        `UserDirectoryPort`가 순위표 한 페이지의 이름을 한 번에 채워야 한다.
        """
        ...
