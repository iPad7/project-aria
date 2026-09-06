"""방송 수명주기 종단 확인 — 실제 Postgres·Redis·Kafka를 태운다.

**유닛 테스트가 못 보는 것만 본다.** 테스트는 Redis를 `fakeredis`로, Kafka를 스텁으로
대신하므로 다음 셋이 통째로 빠진다:

1. `PUBSUB NUMSUB` 가 진짜 Redis에서 우리가 기대하는 수를 세는가 (시청자 판정의 전부다)
2. Postgres가 돌려주는 tz-aware 시각이 `_aware()` 를 거쳐 침묵 계산에 맞게 들어오는가
   (SQLite는 naive라 테스트에서는 다른 경로다)
3. 생성 요청이 실제 브로커로 나가는가

단위마다 스크래치패드에 같은 스크립트를 다시 쓰고 버려 왔다. 여기 두면 다음 단위는
갱신만 하면 되고, 나중에 통합 테스트를 지을 때 이것이 초안이 된다.

**틱을 손으로 돌린다.** 워커를 띄우면 몇 초를 기다려야 하고 실패했을 때 어디서
멈췄는지가 안 보인다. 배선은 `workers.idle.composed()` 를 그대로 빌려 쓰므로 실제
워커와 같은 것을 검증한다.

    docker compose up -d
    uv run alembic -c apps/aria/alembic.ini upgrade head   # (apps/aria 에서)
    uv run python scripts/smoke_lifecycle.py

종료 코드 0이면 통과. 남기는 것: 페르소나 하나와 종료된 방 하나(개발 DB 기준 무해).
"""

from __future__ import annotations

import asyncio
import sys
from uuid import uuid4

from aria.common.config import settings
from aria.common.logging import configure_logging
from aria.common.redis import get_redis
from aria.contexts.chat.adapter.outbound.redis.broadcast import room_channel
from aria.contexts.chat.domain.room import RoomStatus
from aria.workers.idle import composed, tick

# 방치 판정을 기다리지 않기 위해 문턱을 내린다. 30분을 기다릴 수는 없고, 시간을
# 흉내 내면(모킹) 이 스크립트가 검증하려는 실제 시각 처리가 빠진다.
settings.room_abandon_seconds = 2.0
settings.idle_threshold_seconds = 0.0


def _check(passed: bool, what: str) -> bool:
    print(f"  {'OK  ' if passed else 'FAIL'} {what}")
    return passed


async def main() -> int:
    configure_logging()
    redis = get_redis()
    ok = True

    async with composed() as (rooms, progress, closer):
        room = await rooms.open(uuid4(), uuid4(), "스모크 방송")
        await rooms.transition(room.id, RoomStatus.LIVE)
        print(f"\n[1] 방을 열었다 room_id={room.id}")

        # --- 아무도 없을 때 --------------------------------------------------
        print("[2] 시청자 0")
        live = await rooms.get(room.id)
        ok &= _check(
            await progress.advance(live.id, live.persona_id) is None,
            "빈 방에서는 자율발화하지 않는다",
        )
        ok &= _check(
            not await closer.close_if_abandoned(live),
            "막 연 방은 아직 종료되지 않는다",
        )

        # --- 시청자가 붙었을 때 ----------------------------------------------
        pubsub = redis.pubsub()
        await pubsub.subscribe(room_channel(room.id))
        # 구독은 비동기로 확정된다 — 확인 메시지를 받아야 NUMSUB에 잡힌다.
        await pubsub.get_message(timeout=5.0)
        print("[3] 시청자 1 (실제 Redis 구독)")
        try:
            live = await rooms.get(room.id)
            outcome = await progress.advance(live.id, live.persona_id)
            ok &= _check(outcome is not None, "보는 사람이 있으면 말한다")
            if outcome is not None:
                print(f"       source={outcome.source.value} (Kafka로 요청 발행됨)")
            ok &= _check(
                not await closer.close_if_abandoned(live),
                "보고 있는 방은 종료되지 않는다",
            )
        finally:
            await pubsub.unsubscribe(room_channel(room.id))
            await pubsub.aclose()

        # --- 모두 나간 뒤 ----------------------------------------------------
        print(f"[4] 시청자 0, {settings.room_abandon_seconds:.0f}초 침묵")
        await asyncio.sleep(settings.room_abandon_seconds + 0.5)
        live = await rooms.get(room.id)
        ok &= _check(
            await closer.close_if_abandoned(live), "방치된 방은 스스로 종료된다"
        )

        closed = await rooms.get(room.id)
        ok &= _check(closed.status is RoomStatus.FINISHED, "상태가 finished다")
        ok &= _check(
            closed.closed_at is not None and closed.closed_at.tzinfo is not None,
            "closed_at이 tz-aware로 돌아온다 (Postgres 경로)",
        )
        ok &= _check(
            await tick(rooms, progress, closer) == 0,
            "종료된 방은 다음 틱의 대상이 아니다",
        )

    print("\n통과\n" if ok else "\n실패\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
