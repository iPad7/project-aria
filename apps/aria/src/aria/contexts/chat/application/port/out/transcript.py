"""기록 포트 — 방에서 오간 것을 남기고 되읽는다.

**async다.** 소비자가 api의 요청 경로와 생성 워커, 둘 다 async이기 때문이다. 구현은
sync 리포지토리를 `anyio.to_thread`로 넘긴다(`RoomRepository`와 같은 방식) —
포트의 색은 소비자가 정한다(`docs/events.md`).

**쓰기가 실패해도 방송은 계속돼야 한다.** 기록은 부가 기능이고, 그것 때문에 시청자의
메시지가 거부되거나 페르소나가 입을 다물면 안 된다. 그 판단은 부르는 쪽(유스케이스)에
있고 포트는 평범하게 던진다.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aria.contexts.chat.domain.message import RoomMessage


class TranscriptRepository(Protocol):
    async def append(self, message: RoomMessage) -> None: ...

    async def list_recent(
        self, room_id: UUID, *, limit: int = 50, before: UUID | None = None
    ) -> list[RoomMessage]:
        """최신순으로 준다. `before`가 있으면 그 항목보다 **앞선** 것만.

        오프셋이 아니라 커서(`before`)로 페이징한다 — 방송 중에는 새 메시지가 계속
        들어와, 오프셋으로 넘기면 페이지 경계가 밀려 같은 줄을 두 번 보거나 건너뛴다.
        PK가 UUIDv7이라 id 순서가 곧 시간 순서이므로 커서가 id 하나로 끝난다.
        """
        ...
