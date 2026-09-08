"""결제 확정 → outbox → relay.

**이 파일이 지키는 것 세 가지.**

1. **상태 변경과 outbox 로우는 함께 남거나 함께 없다.** 그것이 outbox 패턴의 전부다 —
   하나만 남으면 "결제는 됐는데 크레딧이 없다"거나 그 반대가 된다.
2. **relay는 발행한 것만 표시한다.** 순서가 뒤집히면(표시 → 발행) 죽었을 때 메시지가
   영원히 사라진다. 이 순서면 최악이 중복이고, 중복은 소비자 멱등키가 흡수한다.
3. **멱등키는 결정적이다.** 재발행된 두 메시지가 같은 키를 실어야 원장의 유일 제약이
   두 번째를 막는다. 발행마다 새로 만들면 중복 전달이 곧 이중 지급이다.

소비자 쪽(aria wallet-workers)이 실제로 그 키를 존중하는지는 `test_credit_events.py`가
본다. 두 서비스가 코드를 공유하지 않으므로 계약은 양쪽에서 따로 검증된다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from payments.adapter.outbound.gateway.stub import StubPaymentGateway
from payments.adapter.outbound.persistence.model import OutboxTable, PaymentTable
from payments.adapter.outbound.persistence.repository import (
    SqlModelOutboxRepository,
    SqlModelPaymentRepository,
)
from payments.application.port.out.gateway import Approval, GatewayError
from payments.application.relay import OutboxRelay
from payments.application.service import (
    AmountMismatchError,
    PaymentNotFoundError,
    PaymentService,
)
from payments.application.topics import (
    CREDIT_PURCHASE_CONFIRMED,
    CREDIT_REFUNDED,
    PAYLOAD_VERSION,
)
from payments.domain.model import Payment, PaymentError, PaymentStatus

# --- 하네스 ----------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def service(session: Session) -> PaymentService:
    return PaymentService(SqlModelPaymentRepository(session), StubPaymentGateway())


class RecordingPublisher:
    """`EventPublisher` 구현 — 나간 것을 기록만 한다."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict[str, Any]]] = []

    async def publish(self, topic: str, key: str, payload: dict[str, Any]) -> None:
        self.published.append((topic, key, payload))


class BrokenPublisher:
    """항상 실패하는 발행자 — 죽은 브로커를 흉내 낸다."""

    async def publish(self, topic: str, key: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("브로커가 죽었다")


class HalfBrokenPublisher:
    """첫 건만 성공하고 다음부터 실패한다."""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, topic: str, key: str, payload: dict[str, Any]) -> None:
        if self.published:
            raise RuntimeError("브로커가 죽었다")
        self.published.append(topic)


def _prepare(service: PaymentService, *, amount: int = 10_000, credits: int = 100):
    return service.prepare(uuid4(), f"order-{uuid4().hex[:12]}", amount, credits)


# --- 도메인 ----------------------------------------------------------------


def test_confirming_twice_is_not_an_error() -> None:
    """게이트웨이 webhook은 같은 성공을 여러 번 보낸다 — 재전달이지 오류가 아니다."""
    payment = Payment(user_id=uuid4(), order_id="o1", amount=1000, credits=10)
    payment.confirm("pk-1")
    first_confirmed_at = payment.confirmed_at

    payment.confirm("pk-1")

    assert payment.status is PaymentStatus.CONFIRMED
    # 두 번째 확정이 시각을 밀지 않는다 — 승인 시점은 하나다.
    assert payment.confirmed_at == first_confirmed_at


def test_an_unconfirmed_payment_cannot_be_refunded() -> None:
    payment = Payment(user_id=uuid4(), order_id="o1", amount=1000, credits=10)
    with pytest.raises(PaymentError):
        payment.refund()


def test_a_failed_payment_cannot_be_confirmed() -> None:
    payment = Payment(user_id=uuid4(), order_id="o1", amount=1000, credits=10)
    payment.fail()
    with pytest.raises(PaymentError):
        payment.confirm("pk-1")


# --- 한 트랜잭션 -----------------------------------------------------------


async def test_confirming_writes_the_payment_and_the_outbox_row_together(
    service: PaymentService, session: Session
) -> None:
    payment = _prepare(service, credits=250)

    await service.confirm(payment.order_id, "pk-1")

    stored = session.exec(
        select(PaymentTable).where(PaymentTable.order_id == payment.order_id)
    ).one()
    outbox = session.exec(select(OutboxTable)).all()

    assert stored.status == "confirmed"
    assert len(outbox) == 1
    assert outbox[0].topic == CREDIT_PURCHASE_CONFIRMED
    # 파티션 키는 user_id — 한 사용자의 지급·회수가 같은 파티션에 모여 순서를 지킨다.
    assert outbox[0].key == str(payment.user_id)
    assert outbox[0].payload["credits"] == 250
    assert outbox[0].payload["v"] == PAYLOAD_VERSION
    assert outbox[0].published_at is None


async def test_a_rejected_approval_leaves_nothing_behind(
    session: Session,
) -> None:
    """게이트웨이가 거절하면 결제도 outbox도 그대로다.

    승인 없이 outbox 로우가 생기면 relay가 그것을 내보내 **결제되지 않은 크레딧**이
    지급된다.
    """

    class RejectingGateway:
        async def confirm(self, order_id: str, provider_key: str, amount: int):
            raise GatewayError("승인 거절")

        async def cancel(self, provider_key: str, reason: str) -> None: ...

    service = PaymentService(SqlModelPaymentRepository(session), RejectingGateway())
    payment = _prepare(service)

    with pytest.raises(GatewayError):
        await service.confirm(payment.order_id, "pk-1")

    assert session.exec(select(OutboxTable)).all() == []
    stored = session.exec(
        select(PaymentTable).where(PaymentTable.order_id == payment.order_id)
    ).one()
    assert stored.status == "pending"


async def test_an_amount_mismatch_stops_before_the_outbox(session: Session) -> None:
    """승인 금액이 우리 기록과 다르면 크레딧을 주지 않는다.

    1,000원짜리 주문으로 100,000 크레딧을 받아 가는 경로가 여기서 막힌다.
    """

    class UnderpayingGateway:
        async def confirm(self, order_id: str, provider_key: str, amount: int):
            return Approval(provider_key=provider_key, amount=amount - 1)

        async def cancel(self, provider_key: str, reason: str) -> None: ...

    service = PaymentService(SqlModelPaymentRepository(session), UnderpayingGateway())
    payment = _prepare(service)

    with pytest.raises(AmountMismatchError):
        await service.confirm(payment.order_id, "pk-1")

    assert session.exec(select(OutboxTable)).all() == []


async def test_refunding_records_a_recovery(
    service: PaymentService, session: Session
) -> None:
    payment = _prepare(service, credits=40)
    await service.confirm(payment.order_id, "pk-1")

    await service.refund(payment.order_id)

    topics = [row.topic for row in session.exec(select(OutboxTable)).all()]
    assert topics == [CREDIT_PURCHASE_CONFIRMED, CREDIT_REFUNDED]


async def test_confirming_an_unknown_order_is_not_found(
    service: PaymentService,
) -> None:
    with pytest.raises(PaymentNotFoundError):
        await service.confirm("없는-주문", "pk-1")


# --- 멱등키 ----------------------------------------------------------------


async def test_the_idempotency_key_is_derived_not_generated(
    service: PaymentService, session: Session
) -> None:
    """같은 결제는 몇 번을 발행해도 같은 열쇠를 싣는다.

    relay가 at-least-once라 재발행이 정상 경로다. 키가 매번 달라지면 그 재발행이
    곧 이중 지급이 된다.
    """
    payment = _prepare(service)
    await service.confirm(payment.order_id, "pk-1")

    row = session.exec(select(OutboxTable)).one()
    assert row.payload["idempotency_key"] == f"payment:{payment.id}:purchase"


async def test_purchase_and_refund_do_not_share_a_key(
    service: PaymentService, session: Session
) -> None:
    """지급과 회수가 같은 키를 쓰면 회수가 '이미 적용됨'으로 조용히 무시된다."""
    payment = _prepare(service)
    await service.confirm(payment.order_id, "pk-1")
    await service.refund(payment.order_id)

    keys = {row.payload["idempotency_key"] for row in session.exec(select(OutboxTable))}
    assert len(keys) == 2


# --- relay -----------------------------------------------------------------


async def test_the_relay_publishes_then_marks(
    service: PaymentService, session: Session
) -> None:
    payment = _prepare(service)
    await service.confirm(payment.order_id, "pk-1")
    publisher = RecordingPublisher()
    relay = OutboxRelay(SqlModelOutboxRepository(session), publisher, batch_size=10)

    assert await relay.drain_once() == 1

    topic, key, sent = publisher.published[0]
    assert topic == CREDIT_PURCHASE_CONFIRMED
    assert key == str(payment.user_id)
    assert sent["payment_id"] == str(payment.id)
    assert session.exec(select(OutboxTable)).one().published_at is not None


async def test_a_published_row_is_not_sent_again(
    service: PaymentService, session: Session
) -> None:
    payment = _prepare(service)
    await service.confirm(payment.order_id, "pk-1")
    publisher = RecordingPublisher()
    relay = OutboxRelay(SqlModelOutboxRepository(session), publisher, batch_size=10)

    await relay.drain_once()
    assert await relay.drain_once() == 0
    assert len(publisher.published) == 1


async def test_a_failed_publish_leaves_the_row_unpublished(
    service: PaymentService, session: Session
) -> None:
    """발행이 실패하면 표시하지 않는다 — 다음 폴링이 다시 가져간다.

    **유실을 중복으로 바꾸는 교환**이 성립하는 지점이다. 여기서 표시해 버리면 그
    메시지는 아무도 다시 보내지 않는다.
    """
    payment = _prepare(service)
    await service.confirm(payment.order_id, "pk-1")
    relay = OutboxRelay(
        SqlModelOutboxRepository(session), BrokenPublisher(), batch_size=10
    )

    assert await relay.drain_once() == 0
    assert session.exec(select(OutboxTable)).one().published_at is None

    # 브로커가 돌아오면 같은 로우가 그대로 나간다.
    publisher = RecordingPublisher()
    healthy = OutboxRelay(SqlModelOutboxRepository(session), publisher, batch_size=10)
    assert await healthy.drain_once() == 1


async def test_the_relay_stops_at_the_first_failure(
    service: PaymentService, session: Session
) -> None:
    """건너뛰지 않는다 — 같은 사용자의 지급과 회수가 뒤바뀌면 안 된다."""
    payment = _prepare(service)
    await service.confirm(payment.order_id, "pk-1")
    await service.refund(payment.order_id)

    relay = OutboxRelay(
        SqlModelOutboxRepository(session), HalfBrokenPublisher(), batch_size=10
    )

    assert await relay.drain_once() == 1
    published = [row.published_at for row in session.exec(select(OutboxTable))]
    # 앞의 것만 나갔고 뒤의 것은 남아 있다.
    assert published[0] is not None
    assert published[1] is None


async def test_the_relay_sends_in_recorded_order(
    service: PaymentService, session: Session
) -> None:
    """UUIDv7 PK 오름차순이 곧 기록된 순서다 — 별도 정렬 컬럼이 없는 근거."""
    payment = _prepare(service)
    await service.confirm(payment.order_id, "pk-1")
    await service.refund(payment.order_id)
    publisher = RecordingPublisher()
    relay = OutboxRelay(SqlModelOutboxRepository(session), publisher, batch_size=10)

    await relay.drain_once()

    assert [topic for topic, _, _ in publisher.published] == [
        CREDIT_PURCHASE_CONFIRMED,
        CREDIT_REFUNDED,
    ]


async def test_the_batch_size_caps_one_pass(
    service: PaymentService, session: Session
) -> None:
    """밀린 상황에서 한 바퀴가 무한정 길어지지 않는다 — 남은 것은 다음 폴링이 가져간다."""
    for _ in range(3):
        payment = _prepare(service)
        await service.confirm(payment.order_id, "pk-1")
    relay = OutboxRelay(
        SqlModelOutboxRepository(session), RecordingPublisher(), batch_size=2
    )

    assert await relay.drain_once() == 2
    assert await relay.drain_once() == 1
