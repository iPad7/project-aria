"""outbox-relay 합성 루트 — 기록된 사실을 브로커로 내보내는 타이머.

**api와 별도 진입점이다.** api의 백그라운드 태스크로 두면 api를 재배포할 때마다 relay가
끊기고, 반대로 relay 때문에 api를 못 내린다. 둘의 수명주기가 다르므로 프로세스도
나눈다 — aria가 진행 워커를 api에서 떼어 낸 것과 같은 판단이다.

**relay는 payments 안에 있어야 한다.** outbox 테이블은 이 서비스의 DB 소유다. 밖에서
읽으면 두 서비스가 한 스키마를 공유하게 되고, 그 순간 서비스를 나눈 의미가 없어진다.

실행: `uv run python -m payments.workers.outbox`
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlmodel import Session

from payments.adapter.outbound.events import KafkaEventPublisher
from payments.adapter.outbound.persistence.repository import SqlModelOutboxRepository
from payments.application.relay import OutboxRelay
from payments.common.config import settings
from payments.common.db import engine
from payments.common.kafka import get_broker
from payments.common.logging import configure_logging
from payments.common.topics import PRODUCED, ensure_topics

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def composed() -> AsyncIterator[OutboxRelay]:
    """relay 배선. `run()`과 통합 테스트가 함께 쓴다.

    테스트가 자기 배선을 따로 갖게 두면 실제 워커는 안 도는데 테스트만 통과하는 상태가
    생긴다 — aria의 진행 워커와 같은 이유로 여기도 배선을 하나만 둔다.
    """
    broker = get_broker()
    await broker.connect()
    # **첫 발행보다 먼저 선언한다.** 자동 생성에 맡기면 파티션이 1개가 되고, 그러면
    # 소비자(aria)를 몇 대 띄워도 하나만 일한다 — 소비자 쪽 선언은 이미 생긴 토픽을
    # 고치지 않으므로 그때는 늦다. 근거와 실측은 `common/topics.py`.
    await ensure_topics(PRODUCED)
    try:
        # 세션 하나를 워커 수명 동안 쓴다. 요청-응답이 아니라 긴 루프다.
        with Session(engine) as session:
            yield OutboxRelay(
                SqlModelOutboxRepository(session),
                KafkaEventPublisher(broker),
                batch_size=settings.outbox_batch_size,
            )
    finally:
        # 워커는 이 블록을 빠져나오지 않지만 테스트는 빠져나온다 — 닫지 않으면
        # 프로듀서가 버퍼를 안은 채 남는다.
        await broker.stop()


async def run() -> None:
    async with composed() as relay:
        logger.info(
            "outbox relay 시작 — %.1fs마다 최대 %d건",
            settings.outbox_poll_seconds,
            settings.outbox_batch_size,
        )
        while True:
            try:
                await relay.drain_once()
            except Exception:
                # 루프 자체는 죽지 않는다 — DB나 브로커가 잠깐 흔들려도 다음 폴링에
                # 회복한다. 미발행 로우는 그대로 남아 있으므로 잃는 것이 없다.
                logger.exception("outbox 폴링 실패 — 다음 주기에 재시도한다")
            await asyncio.sleep(settings.outbox_poll_seconds)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
