"""방(Room) 도메인 — 한 페르소나의 방송 한 회차.

레거시의 `ChatRoom`을 옮긴 것이다(`docs/data-model.md`). `persona_id`·`host_id`는 다른
컨텍스트의 엔티티를 가리키지만 **불투명 UUID**일 뿐이다 — chat은 persona도 identity도
import하지 않는다.

**왜 이제야 생겼나.** 그전까지 `room_id`는 클라이언트가 주는 아무 UUID였다. 채팅만
오갈 때는 그래도 굴러갔지만, 후원이 붙자 **존재하지 않는 방에 크레딧을 태울 수**
있게 됐다(차감은 진짜로 일어나고 기록도 남는다). idle 루프도 순회할 방 목록이 없으면
성립하지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import Field

from aria.common.domain import Entity
from aria.common.errors import ConflictError


class RoomStatus(Enum):
    """방송 상태.

    pending: 개설됐지만 시작 전 · live: 방송 중 · finished: 종료.
    """

    PENDING = "pending"
    LIVE = "live"
    FINISHED = "finished"


# 전이는 **전진만** 한다. 되돌리기를 허용하면 "끝난 방송이 다시 살아나는" 상태가
# 생기는데, 시청자에게도 정산에도 아카이브에도 설명할 수 없다. 다시 하려면 새 방을 연다.
_ALLOWED_TRANSITIONS: dict[RoomStatus, frozenset[RoomStatus]] = {
    RoomStatus.PENDING: frozenset({RoomStatus.LIVE, RoomStatus.FINISHED}),
    RoomStatus.LIVE: frozenset({RoomStatus.FINISHED}),
    RoomStatus.FINISHED: frozenset(),
}


class InvalidRoomTransition(ConflictError):
    """허용되지 않는 상태 전이. 이미 끝난 방송을 다시 켜려는 것 같은 경우다."""

    code = "invalid_room_transition"


class Room(Entity):
    persona_id: UUID
    # 방을 연 운영자. 지금은 staff만 방을 열 수 있다(PRD FR-AUTH-3).
    host_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    thumbnail_url: str | None = Field(default=None, max_length=512)
    status: RoomStatus = RoomStatus.PENDING
    # 개설 시각. 방치 판정의 바닥이다 — 활동 기록이 아직 없는 방을 "오래 조용했다"고
    # 오해하지 않으려면 언제부터 셀지가 필요하다.
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # 방송이 끝난 시각. 끝나지 않았으면 None.
    closed_at: datetime | None = None

    def transition_to(self, status: RoomStatus) -> None:
        """상태를 옮긴다. 허용되지 않는 전이면 `InvalidRoomTransition`.

        같은 상태로의 전이도 거부한다 — 멱등하게 보이지만, "이미 라이브인 방을 또
        시작"은 대개 클라이언트가 뭔가 잘못 알고 있다는 뜻이고 조용히 넘기면 그걸
        숨긴다. 좋아요(멱등)와 달리 여기서는 드러내는 편이 낫다.
        """
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise InvalidRoomTransition(
                f"{self.status.value} → {status.value} 전이는 허용되지 않습니다"
            )
        self.status = status
        if status is RoomStatus.FINISHED:
            # 여기서 찍는다 — 종료 경로가 둘(운영자의 finish, 방치 정리)이라 호출자에
            # 맡기면 한쪽이 빠뜨린다. 전이가 한 번뿐이므로(전진만) 덮어쓸 일도 없다.
            self.closed_at = datetime.now(UTC)

    def silent_for(self, since_last_activity: float | None, *, now: datetime) -> float:
        """마지막 활동 이후 흐른 시간(초). 활동 기록이 없으면 개설 이후로 센다.

        기록이 없는 경우는 둘인데 둘 다 이 계산이 맞다: 개설 후 아무 일도 없었거나
        (그러면 개설 이후가 곧 침묵), 활동 키의 TTL이 지났거나(그러면 이미 TTL만큼
        조용했고 개설은 그보다 앞이다).
        """
        if since_last_activity is not None:
            return since_last_activity
        return (now - self.created_at).total_seconds()

    def is_open_for_chat(self) -> bool:
        """채팅·후원을 받을 수 있는 상태인가. 라이브일 때만이다."""
        return self.status is RoomStatus.LIVE
