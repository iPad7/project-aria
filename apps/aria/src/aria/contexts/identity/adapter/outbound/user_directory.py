"""UserDirectoryPort의 identity 구현.

`common.user_directory`의 계약을 identity가 채운다. 이름을 필요로 하는 쪽(community의
열혈순위)은 이 클래스를 모르고, identity도 그쪽을 모른다. 배선은 합성 루트가 한다.

**여기서 필터링을 더 하지 않는다.** 비활성(`is_active=False`) 사용자도 이름을
돌려준다 — 계정이 잠긴 것과 과거의 후원 기록에 이름이 남는 것은 다른 문제이고,
누구를 화면에서 지울지는 그 화면의 결정이지 디렉터리의 결정이 아니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from aria.contexts.identity.application.port.out.repository import UserRepository


class IdentityUserDirectory:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    def display_names(self, user_ids: Sequence[UUID]) -> dict[UUID, str]:
        return {user.id: user.username for user in self._users.list_by_ids(user_ids)}
