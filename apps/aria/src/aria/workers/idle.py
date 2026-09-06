"""progress-worker 합성 루트 — 페르소나가 무슨 말을 할지 정하는 타이머.

`app.py`(api)·`workers/generation.py`(생성)에 이은 셋째 진입점이다. 같은 이미지로
뜨되 진입점만 다르다.

셋 중 하나를 고른다: 선별된 댓글에 답하거나(FR-GEN-1·2), 사연을 읽거나, 자율발화.

**왜 generation-worker에 합치지 않았나.** 그쪽은 FastStream이라 **메시지가 와야
깨어난다**. 여기는 주기적으로 스스로 도는 타이머라 성격이 반대이고, 둘을 한
프로세스에 넣으면 소비와 타이머의 수명주기가 얽혀 종료 처리가 지저분해진다.

**이 워커는 DB를 안다**(방 목록·사연). C-4에서 세운 "워커는 DB를 모른다"는
generation-worker의 규칙이고, 여기는 별개의 워커다 — 생성을 하지 않고 요청만
발행하므로 그 경계와 충돌하지 않는다.

실행: `uv run python -m aria.workers.idle`
"""

from __future__ import annotations

import asyncio
import logging

from sqlmodel import Session

from aria.common.config import settings
from aria.common.db import engine
from aria.common.kafka import KafkaEventBus, get_broker
from aria.common.logging import configure_logging
from aria.common.redis import get_redis
from aria.contexts.chat.adapter.outbound.clustering.lexical import (
    LexicalTopicClusterer,
)
from aria.contexts.chat.adapter.outbound.persistence.repository import (
    SqlModelRoomRepository,
)
from aria.contexts.chat.adapter.outbound.redis.activity import RedisActivityTracker
from aria.contexts.chat.adapter.outbound.redis.audience import RedisRoomAudience
from aria.contexts.chat.adapter.outbound.redis.candidates import RedisCandidateBuffer
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.adapter.outbound.redis.idle_lock import RedisIdleLock
from aria.contexts.chat.application.abandon import AbandonedRoomCloser
from aria.contexts.chat.application.generation import GenerationRequestPublisher
from aria.contexts.chat.application.progress import ProgressService
from aria.contexts.chat.application.room import RoomService
from aria.contexts.community.adapter.outbound.persistence.repository import (
    SqlModelStoryRepository,
)
from aria.contexts.community.adapter.outbound.story_feed import CommunityStoryFeed

configure_logging()
logger = logging.getLogger(__name__)


async def tick(
    rooms: RoomService, progress: ProgressService, closer: AbandonedRoomCloser
) -> int:
    """살아 있는 방을 한 바퀴 돌며 필요한 곳을 진행시킨다. 진행시킨 방 수를 돌려준다.

    순차로 돈다. 병렬화하면 LLM 호출이 한꺼번에 터질 수 있고, 샤딩은 인스턴스가
    여럿일 때 이야기다 — 둘 다 부하가 실측되기 전이라 보류한다. 대신 상한에 걸리는지를
    로그로 남겨 실측 근거를 만든다.

    정리를 진행보다 **먼저** 한다 — 방금 끝낸 방을 같은 틱에 또 진행시키지 않는다.
    """
    live = await rooms.list_live(limit=settings.idle_rooms_per_tick)
    if len(live) == settings.idle_rooms_per_tick:
        logger.warning(
            "한 틱 상한(%d)에 도달했다 — 뒤쪽 방은 다음 틱으로 밀린다",
            settings.idle_rooms_per_tick,
        )

    advanced = 0
    for room in live:
        try:
            if await closer.close_if_abandoned(room):
                continue
            outcome = await progress.advance(room.id, room.persona_id)
            if outcome is not None:
                advanced += 1
                logger.info(
                    "진행 room_id=%s source=%s 후보=%d 토픽=%d",
                    room.id,
                    outcome.source.value,
                    outcome.candidate_count,
                    outcome.topic_count,
                )
        except Exception:
            # 방 하나의 실패가 나머지 방을 멈추게 하면 안 된다.
            logger.exception("방 진행 실패 room_id=%s", room.id)
    return advanced


async def run() -> None:
    redis = get_redis()
    broker = get_broker()
    await broker.connect()

    # 세션 하나를 워커 수명 동안 쓴다. 요청-응답이 아니라 긴 루프라 요청마다
    # 세션을 여는 FastAPI의 방식이 맞지 않는다.
    with Session(engine) as session:
        activity = RedisActivityTracker(redis)
        audience = RedisRoomAudience(redis)
        rooms = RoomService(SqlModelRoomRepository(session))
        progress = ProgressService(
            activity=activity,
            lock=RedisIdleLock(redis),
            stories=CommunityStoryFeed(SqlModelStoryRepository(session)),
            generation=GenerationRequestPublisher(KafkaEventBus(broker)),
            candidates=RedisCandidateBuffer(redis),
            clusterer=LexicalTopicClusterer(),
            coordinator=RedisResponseCoordinator(redis),
            audience=audience,
            threshold_seconds=settings.idle_threshold_seconds,
        )
        closer = AbandonedRoomCloser(
            rooms,
            activity,
            audience,
            abandon_seconds=settings.room_abandon_seconds,
        )
        logger.info(
            "진행 루프 시작 — %.1fs마다 최대 %d개 방, 방치 %.0f분이면 종료",
            settings.idle_tick_seconds,
            settings.idle_rooms_per_tick,
            settings.room_abandon_seconds / 60,
        )
        while True:
            try:
                await tick(rooms, progress, closer)
            except Exception:
                # 루프 자체는 죽지 않는다 — DB나 브로커가 잠깐 흔들려도 다음 틱에 회복한다.
                logger.exception("진행 틱 실패 — 다음 틱에 재시도한다")
            await asyncio.sleep(settings.idle_tick_seconds)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
