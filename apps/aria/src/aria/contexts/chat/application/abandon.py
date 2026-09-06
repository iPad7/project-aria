"""방치된 방 정리 — 아무도 없는 방송을 끝낸다.

**왜 필요한가.** 방을 여는 사람은 있는데 끝내는 사람이 아무도 없었다. 데모로 켠 방
16개가 `live`로 남아 며칠이고 자율발화를 계속했다. 진행 루프에 시청자 검사가
생겼으니(`progress.py`) 비용은 멈추지만, 방은 여전히 영원히 살아 있다 — 목록에
남고, 그 페르소나는 부분 유일 인덱스 때문에 새 방송을 열지 못한다.

**두 조건을 모두 본다: 아무도 안 보고 있고, 오래 조용했다.** 침묵만 보면 위험하다 —
생성이 계속 실패해 진행이 한 번도 성사되지 않는 방은 시청자가 보고 있어도 조용해
보이고, 그러면 사람이 보는 앞에서 방송이 꺼진다. 시청자 수를 함께 보면 그 경우가
빠진다.

정리는 **진행 워커가 한다.** 이미 live 방을 한 바퀴 도는 유일한 프로세스이고, 방을
끝내는 판단에 필요한 것(시청자 수·마지막 활동)을 이미 들고 있다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from aria.common.errors import ConflictError
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.port.out.audience import RoomAudience
from aria.contexts.chat.application.room import RoomService
from aria.contexts.chat.domain.room import Room, RoomStatus

logger = logging.getLogger(__name__)


class AbandonedRoomCloser:
    def __init__(
        self,
        rooms: RoomService,
        activity: ActivityTracker,
        audience: RoomAudience,
        *,
        abandon_seconds: float,
    ) -> None:
        self._rooms = rooms
        self._activity = activity
        self._audience = audience
        self._abandon_seconds = abandon_seconds

    async def close_if_abandoned(self, room: Room) -> bool:
        """방치된 방이면 끝내고 True. 아니면 아무 것도 하지 않고 False."""
        if await self._audience.viewer_count(room.id) > 0:
            return False

        silence = room.silent_for(
            await self._activity.seconds_since_last(room.id), now=datetime.now(UTC)
        )
        if silence < self._abandon_seconds:
            return False

        return await self._close(room.id, silence)

    async def _close(self, room_id: UUID, silence: float) -> bool:
        try:
            await self._rooms.transition(room_id, RoomStatus.FINISHED)
        except ConflictError:
            # 그 사이 운영자가 먼저 끝냈다. 원하던 상태이므로 실패가 아니다.
            return False
        logger.info(
            "방치된 방을 종료했다 room_id=%s 시청자=0 침묵=%.0fs", room_id, silence
        )
        return True
