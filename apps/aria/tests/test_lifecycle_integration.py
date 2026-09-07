"""방송 수명주기 — **실제** Postgres·Redis·Kafka를 타는 통합 테스트.

나머지 테스트가 못 보는 것만 본다. 그쪽은 Redis를 `fakeredis`로, Kafka를 스텁으로,
DB를 인메모리 SQLite로 대신하므로 다음 셋이 통째로 빠진다:

1. `PUBSUB NUMSUB`가 진짜 Redis에서 우리가 기대하는 수를 세는가 — 시청자 판정의 전부다
2. Postgres가 돌려주는 tz-aware 시각이 침묵 계산에 맞게 들어오는가 (SQLite는 naive라
   유닛 테스트에서는 아예 다른 경로다)
3. 생성 요청이 실제 브로커로 나가는가

**배선은 만들지 않고 빌린다.** `workers.idle.composed()`를 그대로 쓴다 — 여기서 따로
배선하면 실제 워커는 안 도는데 테스트만 통과하는 상태가 생기고, 배선이야말로 이
테스트가 검증하려는 것이다.

    docker compose up -d
    cd apps/aria && uv run alembic upgrade head
    uv run pytest -m integration          # 또는 scripts/smoke_lifecycle.py

로직 자체(어떤 순서로 무엇을 고르는가)는 `test_progress_loop.py`가 인프라 없이 훨씬
촘촘히 본다. 여기서 그것을 되풀이하지 않는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlmodel import Session

from aria.common.config import settings
from aria.common.db import engine
from aria.common.redis import get_redis
from aria.contexts.chat.adapter.outbound.persistence.model import RoomTable
from aria.contexts.chat.adapter.outbound.redis.broadcast import room_channel
from aria.contexts.chat.application.abandon import AbandonedRoomCloser
from aria.contexts.chat.application.progress import ProgressService
from aria.contexts.chat.application.room import RoomService
from aria.contexts.chat.domain.room import Room, RoomStatus
from aria.workers.idle import composed

pytestmark = [
    pytest.mark.integration,
    # 모듈 전체가 **하나의 이벤트 루프**를 쓴다. Redis 클라이언트와 Kafka 브로커는
    # 모듈 전역 싱글턴이라(`common/redis.py`·`common/kafka.py`) 처음 쓰인 루프에 연결이
    # 묶인다 — 기본값대로 테스트마다 루프를 새로 만들면 두 번째 테스트가 남의 루프에
    # 달린 퓨처를 기다리다 `got Future attached to a different loop`로 죽는다.
    # 실제 프로세스(워커·api)도 루프 하나로 사는 만큼 이쪽이 운영에 가깝기도 하다.
    pytest.mark.asyncio(loop_scope="module"),
]

# 방치 판정을 실제로 기다릴 수 있는 길이. 운영 기본값은 30분이다.
ABANDON_SECONDS = 2.0

Wiring = tuple[RoomService, ProgressService, AbandonedRoomCloser]


@pytest.fixture
def thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    """방치·침묵 문턱을 기다릴 수 있는 값으로 내린다.

    시계를 모킹하지 않는다 — 실제 시각 처리가 바로 이 파일이 보려는 것이라, 시간을
    흉내 내면 검증 대상이 사라진다. 대신 문턱을 내려 진짜로 기다린다.

    `composed()`가 이 값들을 **생성 시점에** 서비스로 넘기므로 배선보다 먼저 와야
    한다. 아래 `wired`가 이 픽스처에 의존하는 것이 그 순서를 보장한다.
    """
    monkeypatch.setattr(settings, "room_abandon_seconds", ABANDON_SECONDS)
    monkeypatch.setattr(settings, "idle_threshold_seconds", 0.0)


@pytest_asyncio.fixture(loop_scope="module")
async def wired(thresholds: None) -> AsyncIterator[Wiring]:
    """진행 워커와 **같은** 배선."""
    async with composed() as services:
        yield services


@pytest.fixture
def cleanup_rooms() -> Iterator[list[UUID]]:
    """이 테스트가 만든 방을 지우고 나간다.

    개발 DB에 방을 쌓아 두지 않는다. 남은 `live` 방은 다음 실행의 라이브 목록에
    섞여 들어오고, 부분 유일 인덱스 때문에 그 페르소나의 다음 방송을 막는다.
    """
    created: list[UUID] = []
    yield created
    with Session(engine) as session:
        for room_id in created:
            row = session.get(RoomTable, room_id)
            if row is not None:
                session.delete(row)
        session.commit()


@pytest.fixture
def open_live_room(
    wired: Wiring, cleanup_rooms: list[UUID]
) -> Callable[[str], Awaitable[Room]]:
    rooms, _, _ = wired

    async def _open(name: str) -> Room:
        room = await rooms.open(uuid4(), uuid4(), name)
        cleanup_rooms.append(room.id)
        return await rooms.transition(room.id, RoomStatus.LIVE)

    return _open


async def test_an_empty_room_neither_speaks_nor_closes_yet(
    wired: Wiring, open_live_room: Callable[[str], Awaitable[Room]]
) -> None:
    """아무도 없으면 말하지 않는다 — 실제 Redis가 구독자 0을 세는 경로."""
    _, progress, closer = wired
    room = await open_live_room("빈 방")

    assert await progress.advance(room.id, room.persona_id) is None
    # 방치 판정은 침묵도 함께 본다. 막 연 방은 아직 그 문턱에 닿지 않았다.
    assert not await closer.close_if_abandoned(room)


async def test_a_watched_room_speaks_and_is_left_alone(
    wired: Wiring, open_live_room: Callable[[str], Awaitable[Room]]
) -> None:
    """보는 사람이 하나라도 있으면 말하고, 그 방은 닫히지 않는다.

    구독자를 가짜로 세지 않고 진짜 Redis에 구독을 건다. 발화가 성사되면 생성 요청이
    실제 Kafka로 나간 것이기도 하다 — 발행이 실패하면 `advance`가 여기까지 오지 못한다.
    """
    rooms, progress, closer = wired
    room = await open_live_room("보는 사람이 있는 방")

    pubsub = get_redis().pubsub()
    await pubsub.subscribe(room_channel(room.id))
    # 구독은 비동기로 확정된다 — 확인 메시지를 받기 전에는 NUMSUB에 잡히지 않는다.
    assert await pubsub.get_message(timeout=5.0) is not None
    try:
        assert await progress.advance(room.id, room.persona_id) is not None
        assert not await closer.close_if_abandoned(await rooms.get(room.id))
    finally:
        await pubsub.unsubscribe(room_channel(room.id))
        await pubsub.aclose()


async def test_an_abandoned_room_closes_itself(
    wired: Wiring, open_live_room: Callable[[str], Awaitable[Room]]
) -> None:
    """아무도 없고 오래 조용하면 스스로 끝난다.

    데모로 켠 방 16개가 며칠씩 `live`로 남아 자율발화를 계속했던 자리다(#65).
    """
    rooms, _, closer = wired
    room = await open_live_room("방치될 방")

    await asyncio.sleep(ABANDON_SECONDS + 0.5)

    assert await closer.close_if_abandoned(await rooms.get(room.id))

    closed = await rooms.get(room.id)
    assert closed.status is RoomStatus.FINISHED
    # Postgres는 tz-aware로 돌려준다. 침묵 계산이 aware/naive를 섞어 터졌던 자리이고,
    # SQLite는 naive를 돌려주므로 유닛 테스트에서는 이 경로가 아예 없다.
    assert closed.closed_at is not None
    assert closed.closed_at.tzinfo is not None

    # 끝난 방은 다음 틱이 도는 목록에 없다. 여기서 `tick()`을 부르지는 않는다 —
    # 문턱을 내려 둔 채로 돌리면 개발 DB에 살아 있던 남의 방까지 닫아 버린다.
    live = await rooms.list_live(limit=100)
    assert room.id not in {r.id for r in live}
