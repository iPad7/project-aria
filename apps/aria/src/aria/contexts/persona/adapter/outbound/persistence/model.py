"""persona 영속성 테이블.

owner_id는 identity_user를 가리키지만 FK를 걸지 않는다 — 컨텍스트 독립을 물리
스키마에서도 유지한다(참조 무결성은 애플리케이션 규칙으로). 인덱스만 둔다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from aria.common.persistence import TimestampMixin, UUIDMixin


class PersonaTable(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "persona"

    owner_id: UUID = Field(index=True)
    name: str
    tagline: str = ""
    description: str = ""
    is_active: bool = True


class CommunicationStyleTable(TimestampMixin, SQLModel, table=True):
    """말투 — persona와 1:1이라 `persona_id`가 곧 PK다(대리키를 두지 않는다)."""

    __tablename__ = "persona_communication_style"

    persona_id: UUID = Field(primary_key=True)
    tone: str = Field(max_length=100)
    sentence_length: str = Field(default="", max_length=100)
    question_style: str = Field(default="", max_length=200)
    directness: int = Field(default=3)
    empathy_expression: str = Field(default="", max_length=200)

    __table_args__ = (
        CheckConstraint(
            "directness BETWEEN 1 AND 5", name="ck_communication_style_directness"
        ),
    )


class CoreValueTable(UUIDMixin, TimestampMixin, table=True):
    """가치관 어휘. 페르소나들이 공유하므로 이름이 유일하다."""

    __tablename__ = "persona_core_value_vocab"

    value_name: str = Field(max_length=50, unique=True)


class PersonaCoreValueTable(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "persona_core_value"

    persona_id: UUID = Field(index=True)
    # 어휘 테이블은 같은 컨텍스트라 FK를 걸어도 되지만, 이 프로젝트는 조인 대신
    # 애플리케이션에서 맞춘다 — 컨텍스트 안이라도 규약을 일관되게 둔다.
    core_value_id: UUID = Field(index=True)
    priority: int

    __table_args__ = (
        # 같은 가치를 두 번 매달 수 없다.
        UniqueConstraint("persona_id", "core_value_id", name="uq_persona_core_value"),
        # 한 페르소나 안에서 우선순위가 겹치면 정렬이 흔들린다.
        UniqueConstraint(
            "persona_id", "priority", name="uq_persona_core_value_priority"
        ),
    )
