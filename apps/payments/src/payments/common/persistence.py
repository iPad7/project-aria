"""SQLModel 테이블용 믹스인.

주의: 공유 Column 인스턴스는 여러 테이블에 붙일 수 없으므로 sa_column 대신
sa_type + sa_column_kwargs를 써서 서브클래스마다 Column이 새로 만들어지게 한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlmodel import Field, SQLModel

from payments.common.ids import new_id


class UUIDMixin(SQLModel):
    """UUIDv7 기본키."""

    id: uuid.UUID = Field(default_factory=new_id, primary_key=True)


class TimestampMixin(SQLModel):
    """생성/수정 시각 — 값은 DB(server_default/onupdate)에 맡기는 게 관용구."""

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
        nullable=False,
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now(), "onupdate": func.now()},
        nullable=False,
    )
