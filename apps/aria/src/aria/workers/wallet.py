"""wallet-worker 합성 루트 — payments가 보낸 크레딧 사실을 원장에 적용한다.

`app.py`(api)·`generation.py`(생성)·`idle.py`(진행)에 이은 **넷째 진입점**이다. 같은
이미지로 뜨되 진입점만 다르다.

**왜 generation-worker에 합치지 않았나.** 둘 다 FastStream이라 합칠 수는 있지만,
소비 그룹이 다르고(`wallet-workers` vs `generation-workers`) 확장 이유도 다르다 —
생성은 LLM 부하로 늘어나고 크레딧은 결제량으로 늘어난다. 한 프로세스에 두면 둘 중
바쁜 쪽 때문에 다른 쪽까지 복제해야 한다.

실행: `uv run faststream run aria.workers.wallet:app`
"""

from __future__ import annotations

from faststream import FastStream
from sqlmodel import Session

from aria.common.config import settings
from aria.common.db import engine
from aria.common.kafka import KafkaEventBus, get_broker
from aria.common.logging import configure_logging
from aria.common.topics import ensure_topics
from aria.contexts.wallet.adapter.inbound.worker.router import (
    DLQ_SUFFIX,
    CreditEventConsumer,
    register,
)
from aria.contexts.wallet.adapter.outbound.persistence.repository import (
    SqlModelWalletRepository,
)
from aria.contexts.wallet.application.credit_events import (
    CREDIT_PURCHASE_CONFIRMED,
    CREDIT_REFUNDED,
    CreditEventService,
)
from aria.contexts.wallet.application.service import WalletService


def create_app() -> FastStream:
    configure_logging()
    broker = get_broker()

    # 세션 하나를 워커 수명 동안 쓴다. 원장 쓰기는 sync라 핸들러가 `anyio.to_thread`로
    # 넘긴다(어댑터의 일).
    session = Session(engine)
    service = CreditEventService(WalletService(SqlModelWalletRepository(session)))
    register(
        broker,
        CreditEventConsumer(service, KafkaEventBus(broker)),
        group_id=settings.wallet_consumer_group,
    )

    app = FastStream(broker)

    @app.on_startup
    async def declare_topics() -> None:
        # 구독 전에 파티션 수를 못박는다. **payments의 relay도 같은 선언을 한다** —
        # 실제로 발행이 먼저 일어나는 쪽이 payments라, 여기서만 선언하면 이미 1개로
        # 자동 생성된 토픽을 마주하게 된다(그 실측은 `payments/common/topics.py`).
        # 양쪽이 선언하면 누가 먼저 뜨든 3개다. DLQ는 쓰는 쪽이 우리라 여기서만 만든다.
        await ensure_topics(
            [
                CREDIT_PURCHASE_CONFIRMED,
                CREDIT_REFUNDED,
                CREDIT_PURCHASE_CONFIRMED + DLQ_SUFFIX,
                CREDIT_REFUNDED + DLQ_SUFFIX,
            ]
        )

    return app


app = create_app()
