"""chat inbound DI — 전송 중립.

조율 서비스와 활동 트래커의 조립은 HTTP·WebSocket 어느 전송이든 동일하므로
전송별 폴더가 아니라 inbound 최상위에 둔다.

**여기에 LLM도 코디네이터도 없다.** C-4-1에서 생성이 워커로 빠졌기 때문이다 — api는
요청을 큐에 맡길 뿐 무엇이 어떻게 생성하는지 모른다. 그 조립은 워커의 합성 루트
(`aria/workers/generation.py`)에 있다.

`get_superchat`만은 여기서 조립하지 않는다 — 구현은 wallet에 있고 chat은 wallet을
import할 수 없다. 자리만 선언해 두고 실제 구현은 합성 루트(`aria/app.py`)가 꽂는다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from aria.common.eventbus import EventBusPort
from aria.common.kafka import KafkaEventBus, get_broker
from aria.common.redis import get_redis
from aria.common.superchat import SuperchatPort
from aria.contexts.chat.adapter.outbound.redis.activity import RedisActivityTracker
from aria.contexts.chat.adapter.outbound.redis.broadcast import RedisRoomBroadcaster
from aria.contexts.chat.application.generation import GenerationRequestPublisher
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.port.out.broadcast import RoomBroadcaster
from aria.contexts.chat.application.service import ChatOrchestrationService


def get_activity_tracker(
    redis: Annotated[Redis, Depends(get_redis)],
) -> ActivityTracker:
    return RedisActivityTracker(redis)


def get_superchat() -> SuperchatPort:
    """후원 결제 포트의 **자리**. 구현은 합성 루트가 override로 꽂는다.

    chat은 `common.superchat`의 계약만 알고 누가 구현했는지 모른다. FastAPI의
    `dependency_overrides`가 "선언된 제공자를 구체 구현으로 치환"하는 표준 수단이고,
    치환 지점이 곧 합성 루트다. 배선을 잊으면 여기서 시끄럽게 죽는다 — 후원 요청이
    조용히 무시되는 것보다 낫다.
    """
    raise NotImplementedError(
        "SuperchatPort가 배선되지 않았습니다 — aria/app.py의 합성 루트를 확인하세요."
    )


def get_event_bus() -> EventBusPort:
    # 브로커를 만드는 것만으로는 연결하지 않는다 — 발행 시점에 연결된다.
    return KafkaEventBus(get_broker())


def get_chat_service(
    redis: Annotated[Redis, Depends(get_redis)],
    superchat: Annotated[SuperchatPort, Depends(get_superchat)],
    events: Annotated[EventBusPort, Depends(get_event_bus)],
) -> ChatOrchestrationService:
    # 코디네이터·LLM은 여기 없다 — 생성은 워커의 일이라 api는 둘 다 모른다.
    return ChatOrchestrationService(
        activity=RedisActivityTracker(redis),
        broadcaster=RedisRoomBroadcaster(redis),
        generation=GenerationRequestPublisher(events),
        superchat=superchat,
    )


def get_room_broadcaster(
    redis: Annotated[Redis, Depends(get_redis)],
) -> RoomBroadcaster:
    return RedisRoomBroadcaster(redis)
