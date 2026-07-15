"""persona 도메인 모델.

Persona는 스트리머(AI 인격)다. `owner_id`는 이를 만든 사용자(identity)를 가리키지만,
persona 컨텍스트는 identity를 import하지 않는다 — owner_id는 그저 불투명한 UUID다.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from aria.common.domain import Entity


class Persona(Entity):
    owner_id: UUID
    name: str = Field(min_length=1, max_length=30)
    tagline: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=2000)
    is_active: bool = True

    def edit(
        self,
        *,
        name: str | None = None,
        tagline: str | None = None,
        description: str | None = None,
    ) -> None:
        """부분 수정. validate_assignment로 각 대입이 즉시 재검증된다."""
        if name is not None:
            self.name = name
        if tagline is not None:
            self.tagline = tagline
        if description is not None:
            self.description = description
