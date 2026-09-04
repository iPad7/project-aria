"""아웃바운드 포트: 응답 후보 버퍼.

선별이 성립하려면 **모았다가 골라야** 한다. 그전까지는 메시지마다 곧바로 생성 요청이
나갔고, 그래서 "선별"이 Redis 락 경쟁이었다.

**DB가 아니라 휘발 상태다**(`docs/data-model.md`: *"토픽 스레드는 DB에 넣지 않음
— Redis 외부화"*). 오래된 채팅은 후보로서 가치가 없고, 방송이 끝나면 통째로 사라져야
한다. 상한과 TTL을 구현이 지킨다.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aria.contexts.chat.domain.topic import Candidate


class CandidateBuffer(Protocol):
    async def add(self, room_id: UUID, candidate: Candidate) -> None:
        """후보를 담는다. 상한을 넘으면 오래된 것부터 밀려난다."""
        ...

    async def take_all(self, room_id: UUID) -> list[Candidate]:
        """쌓인 후보를 **꺼내면서 비운다**. 오래된 것부터.

        읽기가 아니라 소비인 이유: 고르고 나면 나머지는 버리기 때문이다(기아 방지를
        하지 않는다는 결정). 비우지 않으면 다음 틱에 같은 후보를 또 저울질하게 된다.
        """
        ...
