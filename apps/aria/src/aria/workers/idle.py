"""idle-worker 합성 루트 — 방송이 비어 있지 않게 하는 타이머.

`app.py`(api)·`workers/generation.py`(생성)에 이은 셋째 진입점이다. 같은 이미지로
뜨되 진입점만 다르다.

**왜 generation-worker에 합치지 않았나.** 그쪽은 FastStream이라 **메시지가 와야
깨어난다**. idle은 정반대로 "아무 일도 없을 때" 도는 것이라 타이머가 필요하고, 둘을
한 프로세스에 넣으면 소비와 타이머의 수명주기가 얽혀 종료 처리가 지저분해진다.

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
from aria.common.redis import get_redis
from aria.contexts.chat.adapter.outbound.persistence.repository import (
    SqlModelRoomRepository,
)
from aria.contexts.chat.adapter.outbound.redis.activity import RedisActivityTracker
from aria.contexts.chat.adapter.outbound.redis.idle_lock import RedisIdleLock
from aria.contexts.chat.application.generation import GenerationRequestPublisher
from aria.contexts.chat.application.idle import IdleProgressService
from aria.contexts.chat.application.room import RoomService
from aria.contexts.community.adapter.outbound.persistence.repository import (
    SqlModelStoryRepository,
)
from aria.contexts.community.adapter.outbound.story_feed import CommunityStoryFeed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def tick(rooms: RoomService, idle: IdleProgressService) -> int:
    """살아 있는 방을 한 바퀴 돌며 필요한 곳을 진행시킨다. 진행시킨 방 수를 돌려준다.

    순차로 돈다. 병렬화하면 LLM 호출이 한꺼번에 터질 수 있고, 샤딩은 인스턴스가
    여럿일 때 이야기다 — 둘 다 부하가 실측되기 전이라 보류한다. 대신 상한에 걸리는지를
    로그로 남겨 실측 근거를 만든다.
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
            if await idle.advance(room.id, room.persona_id):
                advanced += 1
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
        rooms = RoomService(SqlModelRoomRepository(session))
        idle = IdleProgressService(
            activity=RedisActivityTracker(redis),
            lock=RedisIdleLock(redis),
            stories=CommunityStoryFeed(SqlModelStoryRepository(session)),
            generation=GenerationRequestPublisher(KafkaEventBus(broker)),
            threshold_seconds=settings.idle_threshold_seconds,
        )
        logger.info(
            "idle 루프 시작 — %.1fs마다 최대 %d개 방",
            settings.idle_tick_seconds,
            settings.idle_rooms_per_tick,
        )
        while True:
            try:
                await tick(rooms, idle)
            except Exception:
                # 루프 자체는 죽지 않는다 — DB나 브로커가 잠깐 흔들려도 다음 틱에 회복한다.
                logger.exception("idle 틱 실패 — 다음 틱에 재시도한다")
            await asyncio.sleep(settings.idle_tick_seconds)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
