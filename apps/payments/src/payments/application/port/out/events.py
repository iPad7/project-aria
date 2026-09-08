"""이벤트 발행 포트.

aria는 이 계약을 커널(`common/eventbus.py`)에 두는데, 거기는 여러 컨텍스트가 같은
백본을 쓰기 때문이다. payments는 컨텍스트가 하나뿐이라 공유할 커널이 없고, 포트는
그것을 쓰는 애플리케이션 옆에 산다.

**발행 전용이다.** payments는 아무것도 구독하지 않는다 — 게이트웨이가 HTTP로 알려 주고,
wallet은 우리가 보낸 것을 받기만 한다.
"""

from __future__ import annotations

from typing import Any, Protocol


class EventPublisher(Protocol):
    async def publish(self, topic: str, key: str, payload: dict[str, Any]) -> None: ...
