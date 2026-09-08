"""결제 확정이 크레딧이 되기까지 — **두 서비스가 실제로 만나는 유일한 자리.**

payments와 aria는 코드를 공유하지 않는다. 둘을 잇는 것은 Kafka 토픽 하나와
`docs/events.md`의 페이로드 표뿐이고, 양쪽 유닛 테스트는 각자 자기 절반만 본다 —
발행 쪽은 자기가 쓴 페이로드를 자기가 읽고, 소비 쪽은 손으로 적은 페이로드를 읽는다.
**둘이 어긋나 있어도 양쪽 다 초록이다.** 여기가 그 틈을 메운다.

실제로 태우는 것:

1. payments의 결제 확정이 **payments DB**에 outbox 로우를 남긴다
2. relay가 그것을 **진짜 Kafka**로 내보낸다 (직렬화·키·토픽 이름까지)
3. 브로커에서 되읽은 페이로드가 aria의 소비자가 읽을 수 있는 모양이다
4. 적용 결과가 **aria DB**의 잔액으로 남는다

FastStream의 구독 배선은 여기서 태우지 않는다 — 살아 있는 컨슈머 그룹의 파티션 배정을
기다리는 것은 느리고 잘 흔들린다. 그 조각은 `test_wallet_worker.py`가 `TestKafkaBroker`로
따로 본다. 여기서 브로커를 직접 읽는 것은 **네 번째 검증(페이로드가 정말 오갔는가)**
자체가 목적이기 때문이다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from aiokafka import AIOKafkaConsumer
from sqlmodel import Session, select

from aria.common.config import settings
from aria.common.db import engine as aria_engine
from aria.contexts.wallet.adapter.inbound.worker.router import CreditEventConsumer
from aria.contexts.wallet.adapter.outbound.persistence.repository import (
    SqlModelWalletRepository,
)
from aria.contexts.wallet.application.credit_events import (
    CREDIT_PURCHASE_CONFIRMED,
    CreditEventService,
)
from aria.contexts.wallet.application.service import WalletService
from payments.adapter.outbound.gateway.stub import StubPaymentGateway
from payments.adapter.outbound.persistence.model import OutboxTable, PaymentTable
from payments.adapter.outbound.persistence.repository import (
    SqlModelPaymentRepository,
)
from payments.application.service import PaymentService
from payments.common.db import engine as payments_engine
from payments.workers.outbox import composed

pytestmark = [
    pytest.mark.integration,
    # Kafka 브로커와 두 엔진이 모듈 전역 싱글턴이라 루프 하나를 공유해야 한다.
    pytest.mark.asyncio(loop_scope="module"),
]

# 브로커에서 우리 메시지가 돌아오기를 기다리는 상한. 넉넉히 두되 무한은 아니다 —
# 안 오면 배관이 끊긴 것이고, 그건 이 테스트가 잡아야 하는 실패다.
_READ_TIMEOUT_SECONDS = 20.0


class _RecordingBus:
    """DLQ 발행자 자리. 여기로 뭔가 나가면 그 자체가 실패 신호다."""

    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)


@pytest.fixture
def payments_session() -> Iterator[Session]:
    with Session(payments_engine) as session:
        yield session


@pytest.fixture
def aria_session() -> Iterator[Session]:
    with Session(aria_engine) as session:
        yield session


@pytest.fixture
def cleanup(payments_session: Session) -> Iterator[list[UUID]]:
    """이 테스트가 payments DB에 남긴 것을 지우고 나간다.

    aria 쪽 원장은 append-only라 지우지 않는다 — 지우는 코드를 두면 그것이 언젠가
    운영에서 불린다. 테스트가 만든 사용자 id는 매번 새로우므로 섞이지 않는다.
    """
    payment_ids: list[UUID] = []
    yield payment_ids

    wanted = {str(payment_id) for payment_id in payment_ids}
    for row in payments_session.exec(select(OutboxTable)).all():
        if row.payload.get("payment_id") in wanted:
            payments_session.delete(row)
    for payment_id in payment_ids:
        row = payments_session.get(PaymentTable, payment_id)
        if row is not None:
            payments_session.delete(row)
    payments_session.commit()


@pytest_asyncio.fixture(loop_scope="module")
async def relay() -> AsyncIterator[Any]:
    """실제 워커와 **같은** 배선. 여기서 따로 조립하면 워커는 안 도는데 테스트만 통과한다."""
    async with composed() as relay:
        yield relay


async def _read_back(payment_id: str) -> dict[str, Any]:
    """브로커에서 우리 메시지를 찾아 온다.

    소비 그룹을 매번 새로 만들고 `earliest`로 읽는다 — 지난 실행이 남긴 메시지도
    함께 오지만 `payment_id`로 걸러 낸다. 살아 있는 그룹의 파티션 배정을 기다리는
    것보다 이쪽이 짧고 흔들리지 않는다.
    """
    consumer = AIOKafkaConsumer(
        CREDIT_PURCHASE_CONFIRMED,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=f"integration-{uuid4().hex[:12]}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda raw: json.loads(raw.decode()),
    )
    await consumer.start()
    try:
        deadline = asyncio.get_running_loop().time() + _READ_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            batch = await consumer.getmany(timeout_ms=1000)
            for records in batch.values():
                for record in records:
                    if record.value.get("payment_id") == payment_id:
                        return record.value
        raise AssertionError(
            f"{_READ_TIMEOUT_SECONDS:.0f}초 안에 메시지가 브로커에서 돌아오지 않았다"
        )
    finally:
        await consumer.stop()


async def test_a_confirmed_payment_becomes_credits_in_another_service(
    relay: Any,
    payments_session: Session,
    aria_session: Session,
    cleanup: list[UUID],
) -> None:
    user_id = uuid4()
    payments = PaymentService(
        SqlModelPaymentRepository(payments_session), StubPaymentGateway()
    )

    # 1) payments DB — 결제 확정과 outbox 로우가 한 트랜잭션에 남는다.
    payment = payments.prepare(user_id, f"order-{uuid4().hex[:12]}", 12_000, 120)
    cleanup.append(payment.id)
    await payments.confirm(payment.order_id, "pk-integration")

    # 2) relay — 진짜 브로커로 나간다.
    assert await relay.drain_once() >= 1

    # 3) 브로커에서 되읽는다. 직렬화·키·토픽 이름이 여기서 처음 실제로 검증된다.
    payload = await _read_back(str(payment.id))
    assert payload["user_id"] == str(user_id)
    assert payload["credits"] == 120
    assert payload["idempotency_key"] == f"payment:{payment.id}:purchase"

    # 4) aria DB — 소비자가 그 페이로드를 읽어 잔액으로 만든다.
    wallets = WalletService(SqlModelWalletRepository(aria_session))
    bus = _RecordingBus()
    consumer = CreditEventConsumer(CreditEventService(wallets), bus)

    await consumer.consume(CREDIT_PURCHASE_CONFIRMED, payload)

    assert bus.published == [], "DLQ로 나갔다 — 두 서비스의 계약이 어긋났다"
    assert wallets.balance(user_id) == 120

    # 재전달은 정상 경로다(relay는 at-least-once). 두 번 와도 한 번만 적용된다.
    await consumer.consume(CREDIT_PURCHASE_CONFIRMED, payload)
    assert wallets.balance(user_id) == 120
