"""아웃바운드 포트: 응답 단일-플라이트 조율 (외부 상태).

레거시 ResponseManager의 인메모리 락을 대체한다. 한 방에서 동시에 하나의 응답만
생성되도록 보장하되, 더 높은 우선순위 소스는 진행 중인 낮은 소스를 선점할 수 있다.
슬롯은 펜싱 토큰을 담아, 선점당한 뒤 뒤늦게 release해도 남의 슬롯을 지우지 않는다.
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

    async def release(self, room_id: UUID, slot: ResponseSlot) -> None:
        """내 슬롯을 놓는다(펜싱 토큰이 일치할 때만)."""
        ...
