"""payments가 보낸 크레딧 사실을 원장에 적용한다 — 소비자 쪽 계약.

**두 서비스가 코드를 공유하지 않으므로 계약은 양쪽에서 따로 검증된다.** 발행 쪽은
`apps/payments/tests/test_payments_outbox.py`가 보고, 여기는 받는 쪽이다. 둘을 잇는
것은 `docs/events.md`의 페이로드 표뿐이라, 이 파일의 페이로드는 그 표를 손으로 옮겨
적은 것이다 — payments의 상수를 import해 오면 그 어긋남을 영영 못 본다.

**여기서 가장 중요한 것은 중복 전달이다.** relay는 at-least-once라 같은 확정이 두 번
오는 것이 정상 경로다. 그게 이중 지급이 되지 않게 하는 것은 `idempotency_key`의 원장
유일 제약 하나뿐이다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from aria.common.eventbus import Event
from aria.contexts.wallet.adapter.inbound.worker.router import (
    DLQ_SUFFIX,
    CreditEventConsumer,
)
from aria.contexts.wallet.adapter.outbound.persistence.repository import (
    SqlModelWalletRepository,
)
from aria.contexts.wallet.application.credit_events import (
    CREDIT_PURCHASE_CONFIRMED,
    CREDIT_REFUNDED,
    CreditEvent,
    CreditEventService,
)
from aria.contexts.wallet.application.service import WalletService
from aria.contexts.wallet.domain.model import TransactionType

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
def wallets(session: Session) -> WalletService:
    return WalletService(SqlModelWalletRepository(session))


@pytest.fixture
def service(wallets: WalletService) -> CreditEventService:
    return CreditEventService(wallets)


class RecordingEventBus:
    """`EventBusPort` 구현 — DLQ로 나간 것을 기록만 한다."""

    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)


@pytest.fixture
def bus() -> RecordingEventBus:
    return RecordingEventBus()


@pytest.fixture
def consumer(
    service: CreditEventService, bus: RecordingEventBus
) -> CreditEventConsumer:
    return CreditEventConsumer(service, bus)


def purchase_payload(
    user_id: UUID, credits: int = 100, payment_id: UUID | None = None
) -> dict[str, Any]:
    """`docs/events.md`의 `payments.credit-purchase-confirmed` 페이로드."""
    pid = payment_id or uuid4()
    return {
        "v": 1,
        "payment_id": str(pid),
        "user_id": str(user_id),
        "credits": credits,
        "idempotency_key": f"payment:{pid}:purchase",
        "confirmed_at": "2026-09-07T00:00:00+00:00",
    }


def refund_payload(
    user_id: UUID, credits: int = 100, payment_id: UUID | None = None
) -> dict[str, Any]:
    pid = payment_id or uuid4()
    return {
        "v": 1,
        "payment_id": str(pid),
        "user_id": str(user_id),
        "credits": credits,
        "idempotency_key": f"payment:{pid}:refund",
        "refunded_at": "2026-09-07T00:00:00+00:00",
    }


# --- 페이로드 해석 ---------------------------------------------------------


def test_an_unknown_payload_version_is_refused() -> None:
    """모르는 버전은 추측하지 않는다 — 반쯤 읽고 엉뚱한 금액을 주는 것이 최악이다."""
    payload = purchase_payload(uuid4()) | {"v": 99}
    with pytest.raises(ValueError, match="버전"):
        CreditEvent.from_payload(payload)


def test_a_payload_without_a_version_is_read_as_v1() -> None:
    payload = purchase_payload(uuid4())
    del payload["v"]
    assert CreditEvent.from_payload(payload).credits == 100


def test_a_non_positive_amount_is_refused() -> None:
    """부호는 토픽이 정한다 — 페이로드의 `credits`는 늘 양수다."""
    with pytest.raises(ValueError, match="양수"):
        CreditEvent.from_payload(purchase_payload(uuid4(), credits=0))


# --- 지급·회수 -------------------------------------------------------------


async def test_a_confirmed_purchase_grants_credits(
    consumer: CreditEventConsumer, wallets: WalletService
) -> None:
    user_id = uuid4()

    await consumer.consume(CREDIT_PURCHASE_CONFIRMED, purchase_payload(user_id, 250))

    assert wallets.balance(user_id) == 250


async def test_a_purchase_is_recorded_as_purchase_not_grant(
    consumer: CreditEventConsumer, wallets: WalletService
) -> None:
    """원장만 보고 "이 크레딧이 어디서 왔나"에 답할 수 있어야 한다."""
    user_id = uuid4()
    payment_id = uuid4()

    await consumer.consume(
        CREDIT_PURCHASE_CONFIRMED, purchase_payload(user_id, 100, payment_id)
    )

    entry = wallets.history(user_id)[0]
    assert entry.type is TransactionType.PURCHASE
    assert entry.ref_id == str(payment_id)


async def test_a_refund_takes_the_credits_back(
    consumer: CreditEventConsumer, wallets: WalletService
) -> None:
    user_id = uuid4()
    payment_id = uuid4()
    await consumer.consume(
        CREDIT_PURCHASE_CONFIRMED, purchase_payload(user_id, 100, payment_id)
    )

    await consumer.consume(CREDIT_REFUNDED, refund_payload(user_id, 100, payment_id))

    assert wallets.balance(user_id) == 0
    assert wallets.history(user_id)[0].type is TransactionType.REFUND


# --- 중복 전달 (이 파일의 핵심) --------------------------------------------


async def test_the_same_confirmation_twice_pays_once(
    consumer: CreditEventConsumer, wallets: WalletService
) -> None:
    """relay가 at-least-once라 재전달은 정상 경로다. 잔액이 두 배가 되면 안 된다."""
    user_id = uuid4()
    payload = purchase_payload(user_id, 300)

    await consumer.consume(CREDIT_PURCHASE_CONFIRMED, payload)
    await consumer.consume(CREDIT_PURCHASE_CONFIRMED, payload)

    assert wallets.balance(user_id) == 300
    assert len(wallets.history(user_id)) == 1


async def test_a_redelivered_refund_recovers_once(
    consumer: CreditEventConsumer, wallets: WalletService
) -> None:
    user_id = uuid4()
    payment_id = uuid4()
    await consumer.consume(
        CREDIT_PURCHASE_CONFIRMED, purchase_payload(user_id, 100, payment_id)
    )
    payload = refund_payload(user_id, 100, payment_id)

    await consumer.consume(CREDIT_REFUNDED, payload)
    await consumer.consume(CREDIT_REFUNDED, payload)

    assert wallets.balance(user_id) == 0


async def test_two_payments_are_not_confused_for_duplicates(
    consumer: CreditEventConsumer, wallets: WalletService
) -> None:
    """멱등키가 결제마다 달라야 두 번 산 사람이 두 번 받는다."""
    user_id = uuid4()

    await consumer.consume(CREDIT_PURCHASE_CONFIRMED, purchase_payload(user_id, 100))
    await consumer.consume(CREDIT_PURCHASE_CONFIRMED, purchase_payload(user_id, 100))

    assert wallets.balance(user_id) == 200


# --- DLQ -------------------------------------------------------------------


async def test_an_unreadable_payload_goes_to_the_dlq(
    consumer: CreditEventConsumer, bus: RecordingEventBus
) -> None:
    await consumer.consume(CREDIT_PURCHASE_CONFIRMED, {"쓰레기": True})

    assert len(bus.published) == 1
    assert bus.published[0].stream == CREDIT_PURCHASE_CONFIRMED + DLQ_SUFFIX
    assert bus.published[0].payload["original"] == {"쓰레기": True}


async def test_refunding_more_than_the_balance_goes_to_the_dlq(
    consumer: CreditEventConsumer, bus: RecordingEventBus, wallets: WalletService
) -> None:
    """이미 써 버린 크레딧의 환불. 음수 잔액으로 두지 않고 사람에게 넘긴다.

    돈이 걸린 일이라 자동으로 봉합하지 않는다 — 환불 취소든 별도 정산이든 정책이
    필요하고, 그건 코드가 정할 것이 아니다.
    """
    user_id = uuid4()

    await consumer.consume(CREDIT_REFUNDED, refund_payload(user_id, 50))

    assert wallets.balance(user_id) == 0
    assert len(bus.published) == 1
    assert bus.published[0].stream == CREDIT_REFUNDED + DLQ_SUFFIX
    assert "InsufficientCreditError" in bus.published[0].payload["error"]


async def test_the_dlq_keeps_the_partition_key(
    consumer: CreditEventConsumer, bus: RecordingEventBus
) -> None:
    """DLQ에서도 사용자별 순서가 남아야 사람이 시간순으로 읽을 수 있다."""
    user_id = uuid4()

    await consumer.consume(CREDIT_REFUNDED, refund_payload(user_id, 10))

    assert bus.published[0].key == str(user_id)
