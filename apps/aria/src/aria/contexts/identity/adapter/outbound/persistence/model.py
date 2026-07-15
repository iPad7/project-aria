"""identity 영속성 테이블. 도메인 User와는 별개의 SQLModel 로우."""

from __future__ import annotations

from sqlmodel import Field

from aria.common.persistence import TimestampMixin, UUIDMixin


class UserTable(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "identity_user"

    email: str = Field(unique=True, index=True)
    username: str
    password_hash: str
    is_active: bool = True
