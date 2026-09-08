"""영속 어댑터 — SQLModel 구현.

**트랜잭션 경계가 여기 있다.** 서비스는 `save_with_outbox()`를 부를 뿐이고, 상태 변경과
outbox 로우가 같은 커밋에 들어간다는 보장은 이 파일의 책임이다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import Session, select

from payments.adapter.outbound.persistence.model import OutboxTable, PaymentTable
from payments.application.port.out.repository import OutboxRepository, PaymentRepository
from payments.domain.model import OutboxMessage, Payment, PaymentStatus


def _to_domain(row: PaymentTable) -> Payment:
    return Payment(
        id=row.id,
        user_id=row.user_id,
        order_id=row.order_id,
        amount=row.amount,
        credits=row.credits,
        status=PaymentStatus(row.status),
        provider_key=row.provider_key,
        confirmed_at=row.confirmed_at,
        refunded_at=row.refunded_at,
    )


def _outbox_to_domain(row: OutboxTable) -> OutboxMessage:
    return OutboxMessage(
        id=row.id,
        topic=row.topic,
        key=row.key,
        payload=row.payload,
        published_at=row.published_at,
    )


class SqlModelPaymentRepository(PaymentRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, payment: Payment) -> None:
        self._session.add(
            PaymentTable(
                id=payment.id,
                user_id=payment.user_id,
                order_id=payment.order_id,
                amount=payment.amount,
                credits=payment.credits,
                status=payment.status.value,
            )
        )
        self._session.commit()

    def get_by_order_id(self, order_id: str) -> Payment | None:
        row = self._session.exec(
            select(PaymentTable).where(PaymentTable.order_id == order_id)
        ).first()
        return _to_domain(row) if row is not None else None

    def save_with_outbox(self, payment: Payment, message: OutboxMessage) -> None:
        """**한 커밋.** 둘 중 하나만 남는 경우가 없다 — 그것이 outbox의 전부다."""
        row = self._session.exec(
            select(PaymentTable).where(PaymentTable.order_id == payment.order_id)
        ).first()
        if row is None:  # 서비스가 읽어 온 결제라 여기서 사라질 일은 없다
            raise LookupError(f"결제를 찾을 수 없습니다: {payment.order_id}")

        row.status = payment.status.value
        row.provider_key = payment.provider_key
        row.confirmed_at = payment.confirmed_at
        row.refunded_at = payment.refunded_at
        self._session.add(row)
        self._session.add(
            OutboxTable(
                id=message.id,
                topic=message.topic,
                key=message.key,
                payload=message.payload,
            )
        )
        self._session.commit()


class SqlModelOutboxRepository(OutboxRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def fetch_unpublished(self, limit: int) -> list[OutboxMessage]:
        rows = self._session.exec(
            select(OutboxTable)
            .where(OutboxTable.published_at.is_(None))  # type: ignore[union-attr]
            # PK가 UUIDv7이라 이 정렬이 곧 기록된 순서다.
            .order_by(OutboxTable.id)  # type: ignore[arg-type]
            .limit(limit)
        ).all()
        # 세션이 오래 사는 워커라, 다음 폴링이 캐시된 로우를 다시 집어 오지 않도록
        # 읽은 것을 놓아 준다.
        self._session.expunge_all()
        return [_outbox_to_domain(row) for row in rows]

    def mark_published(self, message_id: UUID) -> None:
        row = self._session.get(OutboxTable, message_id)
        if row is None:
            return
        row.published_at = datetime.now(UTC)
        self._session.add(row)
        self._session.commit()
