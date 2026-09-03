"""아웃바운드 포트: 응답 단일-플라이트 조율 (외부 상태).

레거시 ResponseManager의 인메모리 락을 대체한다. 한 방에서 동시에 하나의 응답만
생성되도록 보장하되, 더 높은 우선순위 소스는 진행 중인 낮은 소스를 선점할 수 있다.
슬롯은 펜싱 토큰을 담아, 선점당한 뒤 뒤늦게 release해도 남의 슬롯을 지우지 않는다.

**선점은 두 가지를 함께 해야 성립한다**: 새 생성을 못 시작하게 막는 것(`try_acquire`)과,
이미 돌던 생성의 결과를 버리는 것(`still_holds`). 앞의 것만 있으면 선점당한 쪽이 생성을
끝내고 답변을 그대로 발행해 버려서, 슈퍼챗과 밀려난 채팅 응답이 둘 다 나간다 — 우선순위가
장식이 된다. 생성은 취소할 수 없으니(외부 API 호출) **결과를 내보내기 직전에 확인**한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from aria.contexts.chat.domain.source import ChatSource


@dataclass(frozen=True)
class ResponseSlot:
    source: ChatSource
    token: str


class ResponseCoordinator(Protocol):
    async def try_acquire(
        self, room_id: UUID, source: ChatSource
    ) -> ResponseSlot | None:
        """응답 슬롯을 잡는다. 잡으면 ResponseSlot, 더 높은/같은 소스가 점유 중이면 None."""
        ...

    async def still_holds(self, room_id: UUID, slot: ResponseSlot) -> bool:
        """아직 내 슬롯인가. 생성 결과를 내보내도 되는지 판단하는 데 쓴다.

        False면 그 사이 더 높은 우선순위가 선점했다는 뜻이다 — 만들어 둔 응답을 버린다.
        """
        ...

    async def release(self, room_id: UUID, slot: ResponseSlot) -> None:
        """내 슬롯을 놓는다(펜싱 토큰이 일치할 때만)."""
        ...
