"""EventBusPort — shared-kernel port for the durable event backbone (Kafka).

Contexts publish domain events through this port; the FastStream/Kafka adapter
implements it. Keeps domain/application ignorant of Kafka.

Publish-only for now -- consumption happens in worker entrypoints (generation /
media / wallet), which are not built yet. A ``subscribe`` counterpart lands with
the first worker. See docs/architecture.md, docs/events.md.
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
