"""persona 리포지토리 포트의 SQLModel 구현."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from aria.contexts.persona.adapter.outbound.persistence.model import (
    CommunicationStyleTable,
    CoreValueTable,
    PersonaCoreValueTable,
    PersonaTable,
)
from aria.contexts.persona.domain.model import (
    CommunicationStyle,
    CoreValue,
    Persona,
)


def _to_domain(row: PersonaTable) -> Persona:
    return Persona(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        tagline=row.tagline,
        description=row.description,
        is_active=row.is_active,
    )


class SqlModelPersonaRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, persona: Persona) -> None:
        self._session.add(
            PersonaTable(
                id=persona.id,
                owner_id=persona.owner_id,
                name=persona.name,
                tagline=persona.tagline,
                description=persona.description,
                is_active=persona.is_active,
            )
        )
        self._session.commit()

    def get_by_id(self, persona_id: UUID) -> Persona | None:
        row = self._session.get(PersonaTable, persona_id)
        return _to_domain(row) if row is not None else None

    def list_by_owner(self, owner_id: UUID) -> list[Persona]:
        rows = self._session.exec(
            select(PersonaTable).where(PersonaTable.owner_id == owner_id)
        ).all()
        return [_to_domain(row) for row in rows]

    def update(self, persona: Persona) -> None:
        row = self._session.get(PersonaTable, persona.id)
        if row is None:
            return
        row.name = persona.name
        row.tagline = persona.tagline
        row.description = persona.description
        row.is_active = persona.is_active
        self._session.add(row)
        self._session.commit()

    def delete(self, persona: Persona) -> None:
        row = self._session.get(PersonaTable, persona.id)
        if row is not None:
            self._session.delete(row)
            self._session.commit()


class SqlModelProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_style(self, persona_id: UUID) -> CommunicationStyle | None:
        row = self._session.get(CommunicationStyleTable, persona_id)
        if row is None:
            return None
        return CommunicationStyle(
            persona_id=row.persona_id,
            tone=row.tone,
            sentence_length=row.sentence_length,
            question_style=row.question_style,
            directness=row.directness,
            empathy_expression=row.empathy_expression,
        )

    def set_style(self, style: CommunicationStyle) -> None:
        row = self._session.get(CommunicationStyleTable, style.persona_id)
        if row is None:
            row = CommunicationStyleTable(persona_id=style.persona_id, tone=style.tone)
        row.tone = style.tone
        row.sentence_length = style.sentence_length
        row.question_style = style.question_style
        row.directness = style.directness
        row.empathy_expression = style.empathy_expression
        self._session.add(row)
        self._session.commit()

    def ensure_value(self, value_name: str) -> CoreValue:
        row = self._session.exec(
            select(CoreValueTable).where(CoreValueTable.value_name == value_name)
        ).first()
        if row is None:
            row = CoreValueTable(value_name=value_name)
            self._session.add(row)
            try:
                self._session.commit()
            except IntegrityError:
                # 다른 요청이 먼저 만들었다. 원하는 상태(어휘 존재)는 달성됐다.
                self._session.rollback()
                row = self._session.exec(
                    select(CoreValueTable).where(
                        CoreValueTable.value_name == value_name
                    )
                ).one()
        return CoreValue(id=row.id, value_name=row.value_name)

    def set_core_values(self, persona_id: UUID, value_names: Sequence[str]) -> None:
        # 통째로 교체한다 — 우선순위가 목록의 순서라 부분 수정이 성립하지 않는다.
        # 지우고 다시 넣는 것을 **한 트랜잭션**에 두어야 중간 상태가 보이지 않는다.
        for row in self._session.exec(
            select(PersonaCoreValueTable).where(
                PersonaCoreValueTable.persona_id == persona_id
            )
        ).all():
            self._session.delete(row)
        self._session.flush()  # 유일 제약 충돌을 피하려면 지운 뒤에 넣어야 한다

        for priority, name in enumerate(value_names, start=1):
            value = self.ensure_value(name)
            self._session.add(
                PersonaCoreValueTable(
                    persona_id=persona_id,
                    core_value_id=value.id,
                    priority=priority,
                )
            )
        self._session.commit()

    def list_core_values(self, persona_id: UUID) -> list[str]:
        rows = self._session.exec(
            select(CoreValueTable.value_name)
            .join(
                PersonaCoreValueTable,
                col(PersonaCoreValueTable.core_value_id) == col(CoreValueTable.id),
            )
            .where(PersonaCoreValueTable.persona_id == persona_id)
            .order_by(col(PersonaCoreValueTable.priority))
        ).all()
        return list(rows)
