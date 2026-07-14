"""EventBusPort — shared-kernel port for the durable event backbone (Kafka).

Contexts publish/subscribe domain events through this port; the FastStream/Kafka
adapter implements it. Keeps domain/application ignorant of Kafka.
See docs/architecture.md, docs/events.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Event:
    stream: str  # e.g. "aria.chat.response-requested"
    key: str  # partition key (e.g. room_id) for ordering
    payload: dict[str, Any]


class EventBusPort(Protocol):
    async def publish(self, event: Event) -> None: ...
