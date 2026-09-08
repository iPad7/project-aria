"""HTTP 요청·응답 스키마 — 경계에서만 쓰는 모양."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from payments.domain.model import Payment


class PrepareRequest(BaseModel):
    user_id: UUID
    order_id: str = Field(min_length=1, max_length=64)
    amount: int = Field(gt=0)
    credits: int = Field(gt=0)


class ConfirmRequest(BaseModel):
    """게이트웨이 확정 요청.

    **금액을 받지 않는다.** 우리가 `prepare`에서 기록해 둔 값이 기준이고, 클라이언트가
    보내온 금액을 믿으면 대조가 자기 자신과의 비교가 된다.
    """

    order_id: str = Field(min_length=1, max_length=64)
    provider_key: str = Field(min_length=1, max_length=128)


class RefundRequest(BaseModel):
    reason: str = Field(default="요청에 의한 환불", max_length=200)


class PaymentResponse(BaseModel):
    id: UUID
    user_id: UUID
    order_id: str
    amount: int
    credits: int
    status: str
    confirmed_at: datetime | None
    refunded_at: datetime | None

    @classmethod
    def of(cls, payment: Payment) -> PaymentResponse:
        # `provider_key`는 내보내지 않는다 — 게이트웨이 식별자가 밖으로 나갈 이유가 없다.
        return cls(
            id=payment.id,
            user_id=payment.user_id,
            order_id=payment.order_id,
            amount=payment.amount,
            credits=payment.credits,
            status=payment.status.value,
            confirmed_at=payment.confirmed_at,
            refunded_at=payment.refunded_at,
        )
