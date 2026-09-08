"""결제 도메인 — 결제 하나와, 그 결제가 밖으로 내보낼 사실 하나.

**두 모델이 한 파일에 있는 이유.** `OutboxMessage`는 별개의 애그리게이트가 아니라
`Payment`의 상태 변경이 남기는 **부산물**이다. 둘은 반드시 같은 트랜잭션에 기록되므로
(`docs/events.md`의 outbox 규약) 떼어 놓으면 그 불변식이 파일 경계에 가려진다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from payments.common.ids import new_id


class PaymentStatus(Enum):
    """결제 수명주기.

    `failed`와 `refunded`가 모두 종착지다 — 전자는 돈이 오간 적 없고, 후자는 오갔다가
    되돌아왔다. 크레딧 관점에서 완전히 다른 일이라 한 상태로 합치지 않는다.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentError(Exception):
    """허용되지 않는 상태 전이."""


class Payment(BaseModel):
    """크레딧 구매 한 건.

    `order_id`는 **우리가 만들어 게이트웨이에 넘기는** 주문번호이고, `provider_key`는
    게이트웨이가 돌려주는 식별자다. 둘을 한 필드로 합치지 않는 이유: 확정 전에는
    `provider_key`가 없는데 그 사이에도 결제를 조회할 수 있어야 한다.
    """

    id: UUID = Field(default_factory=new_id)
    user_id: UUID
    order_id: str = Field(min_length=1, max_length=64)
    # 원 단위. 부동소수를 쓰지 않는다 — 돈에 반올림 오차가 끼면 대사가 안 맞는다.
    amount: int = Field(gt=0)
    # 확정되면 지급할 크레딧. 환율(원↔크레딧)을 결제 시점에 못박아 두는 것이라,
    # 나중에 정책이 바뀌어도 과거 결제의 지급량이 흔들리지 않는다.
    credits: int = Field(gt=0)
    status: PaymentStatus = PaymentStatus.PENDING
    provider_key: str | None = Field(default=None, max_length=128)
    confirmed_at: datetime | None = None
    refunded_at: datetime | None = None

    def confirm(self, provider_key: str, *, now: datetime | None = None) -> None:
        """게이트웨이가 승인했다. `pending`에서만 갈 수 있다.

        이미 `confirmed`인 것을 다시 확정하려는 것은 **에러가 아니라 재전달**이다 —
        게이트웨이 webhook은 같은 성공을 여러 번 보낸다. 그래서 조용히 돌아간다:
        원하던 최종 상태가 이미 달성돼 있다.
        """
        if self.status is PaymentStatus.CONFIRMED:
            return
        if self.status is not PaymentStatus.PENDING:
            raise PaymentError(f"확정할 수 없는 결제입니다: {self.status.value}")
        self.status = PaymentStatus.CONFIRMED
        self.provider_key = provider_key
        self.confirmed_at = now or datetime.now(UTC)

    def fail(self) -> None:
        if self.status is not PaymentStatus.PENDING:
            raise PaymentError(f"실패 처리할 수 없는 결제입니다: {self.status.value}")
        self.status = PaymentStatus.FAILED

    def refund(self, *, now: datetime | None = None) -> None:
        """확정된 결제만 환불된다. 확정 전 취소는 `fail()`이다."""
        if self.status is PaymentStatus.REFUNDED:
            return
        if self.status is not PaymentStatus.CONFIRMED:
            raise PaymentError(f"환불할 수 없는 결제입니다: {self.status.value}")
        self.status = PaymentStatus.REFUNDED
        self.refunded_at = now or datetime.now(UTC)


class OutboxMessage(BaseModel):
    """밖으로 내보내야 할 사실 하나. 아직 나갔는지는 `published_at`이 안다.

    **왜 곧바로 Kafka에 발행하지 않는가.** DB 커밋과 브로커 발행은 하나의 트랜잭션이
    될 수 없다. 커밋 후 발행하면 그 사이 죽었을 때 **결제는 됐는데 크레딧이 없고**,
    발행 후 커밋하면 **크레딧은 줬는데 결제 기록이 없다**. 로우로 함께 커밋해 두고
    나중에 relay가 내보내면 둘 다 일어나지 않는다 — 남는 위험은 중복 발행뿐이고,
    그건 소비자 쪽 멱등키가 흡수한다.
    """

    id: UUID = Field(default_factory=new_id)
    topic: str = Field(min_length=1, max_length=200)
    # Kafka 파티션 키. 같은 키는 같은 파티션이라 한 사용자의 지급·회수 순서가 남는다.
    key: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any]
    published_at: datetime | None = None
