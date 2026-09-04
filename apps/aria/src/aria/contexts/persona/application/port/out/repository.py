"""아웃바운드 포트: 페르소나 영속성."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from aria.contexts.persona.domain.model import (
    CommunicationStyle,
    CoreValue,
    Persona,
)


class PersonaRepository(Protocol):
    def add(self, persona: Persona) -> None: ...

    def get_by_id(self, persona_id: UUID) -> Persona | None: ...

    def list_by_owner(self, owner_id: UUID) -> list[Persona]: ...

    def update(self, persona: Persona) -> None: ...

    def delete(self, persona: Persona) -> None: ...


class ProfileRepository(Protocol):
    """말투·가치관. `PersonaRepository`와 나눈 이유는 수명이 다르기 때문이다 —
    페르소나는 CRUD로 자주 오가지만 프로필은 거의 안 바뀌고 읽기가 압도적이다.
    """

    def get_style(self, persona_id: UUID) -> CommunicationStyle | None: ...

    def set_style(self, style: CommunicationStyle) -> None:
        """말투를 저장한다(있으면 덮어쓴다). 1:1이라 upsert가 자연스럽다."""
        ...

    def ensure_value(self, value_name: str) -> CoreValue:
        """가치관 어휘를 확보한다. 이미 있으면 그것을 돌려준다.

        어휘를 별도로 만들게 하지 않는 이유: 관리자가 "정직"을 매달려고 할 때
        그 단어가 이미 등록됐는지를 신경 쓸 이유가 없다.
        """
        ...

    def set_core_values(self, persona_id: UUID, value_names: Sequence[str]) -> None:
        """가치관 목록을 **통째로 교체**한다. 순서가 곧 우선순위다.

        부분 수정이 아니라 교체인 이유: 우선순위가 목록의 순서라서, 하나만 빼거나
        넣으면 나머지 순위가 전부 밀린다. 그럴 바에는 원하는 최종 상태를 받는 편이
        호출하는 쪽에도 단순하다.
        """
        ...

    def list_core_values(self, persona_id: UUID) -> list[str]:
        """우선순위 순으로 가치관 이름을 돌려준다."""
        ...
