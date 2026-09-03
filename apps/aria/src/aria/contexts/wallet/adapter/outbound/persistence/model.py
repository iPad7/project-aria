"""wallet 영속성 테이블.

`user_id`·`persona_id`·`room_id`는 다른 컨텍스트를 가리키지만 **FK를 걸지 않는다** —
컨텍스트 독립을 물리 스키마까지 관철한다(`docs/architecture.md`). 인덱스만 둔다.

`type`은 문자열로 저장한다(community의 `status`와 같은 이유 — DB enum은 값 추가마다
마이그레이션을 요구한다). 유효성은 도메인 `TransactionType`이 강제한다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, Index, text
from sqlmodel import Field, SQLModel

from aria.common.persistence import TimestampMixin, UUIDMixin


class WalletTable(TimestampMixin, SQLModel, table=True):
    __tablename__ = "wallet_wallet"

    # UUIDMixin을 쓰지 않는다 — PK가 대리키가 아니라 user_id다(1:1).
    user_id: UUID = Field(primary_key=True)
    credit_balance: int = Field(default=0)

    # 앱 버그로도 마이너스 잔액이 남지 않게 DB가 마지막 방어선을 선다.
    __table_args__ = (
        CheckConstraint("credit_balance >= 0", name="ck_wallet_balance_non_negative"),
    )


class CreditTransactionTable(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "wallet_credit_transaction"

    user_id: UUID
    delta: int
    type: str
    ref_id: str | None = None
    # 멱등 지급의 관문. 동시 요청 두 개를 앱 검사로는 막을 수 없으므로 DB가 강제한다.
    # nullable + unique이며, NULL은 서로 중복으로 보지 않는다(후원처럼 매번 새 건).
    idempotency_key: str | None = Field(default=None, unique=True)

    # 내 원장 조회: user_id 필터 + created_at 최신순.
    __table_args__ = (
        Index(
            "ix_wallet_credit_transaction_user_created",
            "user_id",
            text("created_at DESC"),
        ),
    )


class DonationTable(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "wallet_donation"

    persona_id: UUID
    donor_id: UUID | None = Field(default=None, index=True)
    room_id: UUID | None = Field(default=None, index=True)
    amount: int
    message: str | None = None

    # 방송국 후원 목록/열혈순위(C-3)가 타는 인덱스.
    __table_args__ = (
        Index(
            "ix_wallet_donation_persona_created",
            "persona_id",
            text("created_at DESC"),
        ),
    )
