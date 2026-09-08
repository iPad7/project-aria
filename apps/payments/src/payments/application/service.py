"""결제 유스케이스 — 준비·확정·환불.

**이 서비스는 Kafka를 모른다.** 확정의 결과로 남기는 것은 outbox 로우 하나이고, 그것이
언제 어떻게 브로커로 나가는지는 relay의 일이다(`relay.py`). 그 분리가 outbox 패턴의
핵심이다 — 유스케이스는 로컬 트랜잭션 하나만 신경 쓰면 된다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from payments.application.port.out.gateway import GatewayError, PaymentGatewayPort
from payments.application.port.out.repository import PaymentRepository
from payments.application.topics import (
    CREDIT_PURCHASE_CONFIRMED,
    CREDIT_REFUNDED,
    PAYLOAD_VERSION,
    purchase_idempotency_key,
    refund_idempotency_key,
)
from payments.domain.model import OutboxMessage, Payment, PaymentError

logger = logging.getLogger(__name__)


class PaymentNotFoundError(Exception):
    pass


class AmountMismatchError(Exception):
    """게이트웨이가 승인한 금액이 우리가 기록한 금액과 다르다.

    조작된 결제 요청의 전형이다. 승인은 이미 났으므로 여기서 멈추면 **돈은 받고
    크레딧은 안 준 상태**가 되지만, 반대(적게 받고 많이 주기)보다 낫고 사람이
    개입할 수 있다.
    """


class PaymentService:
    def __init__(
        self, payments: PaymentRepository, gateway: PaymentGatewayPort
    ) -> None:
        self._payments = payments
        self._gateway = gateway

    def prepare(
        self, user_id: UUID, order_id: str, amount: int, credits: int
    ) -> Payment:
        """결제창을 띄우기 전에 `pending` 한 줄을 남긴다.

        **왜 미리 기록하는가.** 확정 시점에 "이 주문이 얼마짜리였나"를 우리 쪽 기록으로
        답할 수 있어야 게이트웨이가 알려 준 금액을 대조할 수 있다. 클라이언트가 보내온
        금액을 믿으면 대조가 자기 자신과의 비교가 된다.
        """
        payment = Payment(
            user_id=user_id, order_id=order_id, amount=amount, credits=credits
        )
        self._payments.add(payment)
        return payment

    async def confirm(self, order_id: str, provider_key: str) -> Payment:
        """게이트웨이 승인을 확정하고, 크레딧 지급 사실을 outbox에 남긴다."""
        payment = self._require(order_id)

        approval = await self._gateway.confirm(order_id, provider_key, payment.amount)
        if approval.amount != payment.amount:
            raise AmountMismatchError(
                f"승인 금액이 다릅니다: 기록={payment.amount} 승인={approval.amount}"
            )

        try:
            payment.confirm(approval.provider_key)
        except PaymentError:
            # 확정할 수 없는 상태(실패·환불됨)다. 승인이 났는데 우리 쪽이 못 받는
            # 상황이라 사람이 봐야 한다 — 게이트웨이 취소는 여기서 하지 않는다.
            logger.exception("확정할 수 없는 결제 order_id=%s", order_id)
            raise

        self._payments.save_with_outbox(
            payment,
            OutboxMessage(
                topic=CREDIT_PURCHASE_CONFIRMED,
                # 같은 사용자의 지급·회수가 한 파티션에 모여 순서를 유지한다.
                key=str(payment.user_id),
                payload={
                    "v": PAYLOAD_VERSION,
                    "payment_id": str(payment.id),
                    "user_id": str(payment.user_id),
                    "credits": payment.credits,
                    "idempotency_key": purchase_idempotency_key(str(payment.id)),
                    "confirmed_at": _iso(payment.confirmed_at),
                },
            ),
        )
        logger.info(
            "결제 확정 order_id=%s payment_id=%s credits=%d",
            order_id,
            payment.id,
            payment.credits,
        )
        return payment

    async def refund(self, order_id: str, reason: str = "요청에 의한 환불") -> Payment:
        """환불하고, 크레딧 회수 사실을 outbox에 남긴다.

        **게이트웨이를 먼저 부른다.** 우리 상태를 먼저 바꾸면 게이트웨이 취소가 실패했을
        때 "환불됨"으로 남은 결제의 돈이 실제로는 그대로다. 반대 순서면 최악이 "돈은
        돌려줬는데 우리 기록은 confirmed" — 재시도로 수렴한다.
        """
        payment = self._require(order_id)
        if payment.provider_key is None:
            raise PaymentError("승인되지 않은 결제는 환불할 수 없습니다")

        await self._gateway.cancel(payment.provider_key, reason)
        payment.refund()

        self._payments.save_with_outbox(
            payment,
            OutboxMessage(
                topic=CREDIT_REFUNDED,
                key=str(payment.user_id),
                payload={
                    "v": PAYLOAD_VERSION,
                    "payment_id": str(payment.id),
                    "user_id": str(payment.user_id),
                    "credits": payment.credits,
                    "idempotency_key": refund_idempotency_key(str(payment.id)),
                    "refunded_at": _iso(payment.refunded_at),
                },
            ),
        )
        logger.info("결제 환불 order_id=%s payment_id=%s", order_id, payment.id)
        return payment

    def get(self, order_id: str) -> Payment:
        return self._require(order_id)

    def _require(self, order_id: str) -> Payment:
        payment = self._payments.get_by_order_id(order_id)
        if payment is None:
            raise PaymentNotFoundError(f"결제를 찾을 수 없습니다: {order_id}")
        return payment


def _iso(value: datetime | None) -> str:
    """이벤트에 실을 시각. 도메인이 채워 두므로 `None`이면 프로그래밍 오류다."""
    return (value or datetime.now(UTC)).isoformat()


__all__ = [
    "AmountMismatchError",
    "GatewayError",
    "PaymentNotFoundError",
    "PaymentService",
]
