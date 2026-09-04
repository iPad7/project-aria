"""EventBusPort — durable 이벤트 백본(Kafka)의 공유 커널 포트.

컨텍스트는 이 포트로 이벤트를 발행하고, FastStream/Kafka 어댑터가 구현한다.
도메인·애플리케이션은 Kafka를 모른다. 근거는 `docs/architecture.md`, `docs/events.md`.

**발행 전용이며, 앞으로도 그렇다.** 이전 판의 docstring은 "첫 워커와 함께 `subscribe`
짝이 들어온다"고 적어 두었으나 C-4-1에서 그러지 않기로 했다 — **워커 진입점 자체가
인바운드 어댑터**이기 때문이다. HTTP 라우터가 요청을 받아 애플리케이션 서비스를
부르듯, 워커는 메시지를 받아 같은 일을 한다. 포트는 애플리케이션이 **밖을 부를 때**
필요한 것이고, 밖에서 안으로 들어오는 방향은 어댑터가 서비스를 직접 부른다.
`subscribe`를 Protocol로 감싸면 FastStream의 데코레이터 구독과 싸우면서 얻는 게 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Event:
    stream: str  # 예: "aria.chat.response-requested"
    key: str  # 파티션 키(예: room_id) — 같은 키는 같은 파티션이라 순서가 보장된다
    payload: dict[str, Any]


class EventBusPort(Protocol):
    async def publish(self, event: Event) -> None: ...
