"""UserRepository 포트의 SQLModel 구현."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlmodel import Session, col, select

from aria.contexts.identity.adapter.outbound.persistence.model import UserTable
from aria.contexts.identity.domain.model import User


def _to_domain(row: UserTable) -> User:
    return User(
        id=row.id,
        email=row.email,
        username=row.username,
        password_hash=row.password_hash,
        is_active=row.is_active,
        is_staff=row.is_staff,
    )


class SqlModelUserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, user: User) -> None:
        self._session.add(
            UserTable(
                id=user.id,
                email=user.email,
                username=user.username,
                password_hash=user.password_hash,
                is_active=user.is_active,
                is_staff=user.is_staff,
            )
        )
        self._session.commit()

    def get_by_email(self, email: str) -> User | None:
        row = self._session.exec(
            select(UserTable).where(UserTable.email == email)
        ).first()
        return _to_domain(row) if row is not None else None

    def get_by_id(self, user_id: UUID) -> User | None:
        row = self._session.get(UserTable, user_id)
        return _to_domain(row) if row is not None else None

    def list_by_ids(self, user_ids: Sequence[UUID]) -> list[User]:
        if not user_ids:
            # 빈 IN 절은 DB마다 다루기가 다르다. 어차피 결과가 없으니 묻지 않는다.
            return []
        rows = self._session.exec(
            select(UserTable).where(col(UserTable.id).in_(user_ids))
        ).all()
        return [_to_domain(row) for row in rows]
