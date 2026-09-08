"""payments 테이블 정의.

`user_id`는 aria의 사용자를 가리키지만 **FK가 없다**(인덱스만) — 다른 서비스의 다른
DB에 있는 로우라 걸 수가 없다. aria가 컨텍스트 간 cross-context FK를 걸지 않는 것과
같은 판단이, 서비스 경계에서는 물리적 강제가 된다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Index, text
from sqlmodel import Field

from payments.common.persistence import TimestampMixin, UUIDMixin


class PaymentTable(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "payment"

    user_id: UUID = Field(index=True)
    # 게이트웨이에 넘긴 주문번호. **유일해야 한다** — 확정 요청이 이 값으로 결제를
    # 찾으므로, 겹치면 남의 결제를 확정하게 된다.
    order_id: str = Field(max_length=64, unique=True, index=True)
    amount: int
    credits: int
    status: str = Field(default="pending", index=True)
    provider_key: str | None = Field(default=None, max_length=128)
    confirmed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    refunded_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class OutboxTable(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "outbox"

    topic: str = Field(max_length=200)
    key: str = Field(max_length=200)
    payload: dict[str, Any] = Field(sa_type=JSON)
    # 나갔으면 그 시각, 아직이면 NULL. 별도 boolean을 두지 않는다 — "언제 나갔나"는
    # 대사할 때 실제로 필요한 값이고, NULL 여부가 곧 미발행 표시다.
    published_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    __table_args__ = (
        # relay가 매 폴링마다 타는 유일한 조회다. **부분 인덱스**여야 한다: 발행된
        # 로우는 시간이 갈수록 쌓이기만 하는데, 전체 인덱스면 그 죽은 무게를 매초
        # 끌고 다닌다. 조건을 걸면 인덱스 크기가 "아직 안 나간 것" 수에 머문다.
        Index(
            "ix_outbox_unpublished",
            "id",
            postgresql_where=text("published_at IS NULL"),
            sqlite_where=text("published_at IS NULL"),
        ),
    )
