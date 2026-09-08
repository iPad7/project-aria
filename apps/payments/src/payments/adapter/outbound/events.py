"""`EventPublisher`의 Kafka 구현.

**연결은 첫 발행 때 한다.** relay 워커는 자기 루프에서 미리 연결하지만, api도 언젠가
이 어댑터를 쓸 수 있고 FastAPI는 브로커 생명주기를 잡아 주지 않는다. aria가 같은 자리에서
겪은 `IncorrectState` 크래시를 되풀이하지 않으려고 같은 방식을 쓴다.
"""

from __future__ import annotations

import asyncio
from typing import Any

from faststream.kafka import KafkaBroker

_connect_lock = asyncio.Lock()


class KafkaEventPublisher:
    def __init__(self, broker: KafkaBroker) -> None:
        self._broker = broker

    async def publish(self, topic: str, key: str, payload: dict[str, Any]) -> None:
        await self._ensure_connected()
        await self._broker.publish(payload, topic=topic, key=key.encode())

    async def _ensure_connected(self) -> None:
        if self._broker.running:
            return
        async with _connect_lock:
            if not self._broker.running:
                await self._broker.connect()
