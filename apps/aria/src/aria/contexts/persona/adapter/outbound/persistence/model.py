"""persona 영속성 테이블.

owner_id는 identity_user를 가리키지만 FK를 걸지 않는다 — 컨텍스트 독립을 물리
스키마에서도 유지한다(참조 무결성은 애플리케이션 규칙으로). 인덱스만 둔다.
"""

from __future__ import annotations

from uuid import UUID

from sqlmodel import Field

from aria.common.persistence import TimestampMixin, UUIDMixin


class PersonaTable(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "persona"

    owner_id: UUID = Field(index=True)
    name: str
    tagline: str = ""
    description: str = ""
    is_active: bool = True
