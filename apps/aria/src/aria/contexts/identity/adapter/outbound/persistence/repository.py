"""UserRepository 포트의 SQLModel 구현."""

from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, select

from aria.contexts.identity.adapter.outbound.persistence.model import UserTable
from aria.contexts.identity.domain.model import User


def _to_domain(row: UserTable) -> User:
    return User(
        id=row.id,
        email=row.email,
        username=row.username,
        password_hash=row.password_hash,
        is_active=row.is_active,
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
