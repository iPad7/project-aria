"""도메인 엔티티 base.

Pydantic으로 표현해 생성·변경 시점에 불변식을 강제한다(잘못된 상태의 객체가
태어나지 못하게). 이것은 API DTO(SchemaBase)도, ORM 로우(persistence)도 아니다 —
오직 도메인 불변식만 담는다. 결정 배경은 ADR-0007(위키).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from aria.common.ids import new_id


class Entity(BaseModel):
    """식별자를 가진 도메인 엔티티의 base."""

    model_config = ConfigDict(
        validate_assignment=True,  # 속성 재할당 시에도 재검증 → 변경 후에도 불변식 유지
        extra="forbid",
    )

    id: uuid.UUID = Field(default_factory=new_id, frozen=True)
