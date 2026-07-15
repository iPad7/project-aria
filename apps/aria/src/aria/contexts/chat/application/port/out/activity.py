"""아웃바운드 포트: 방 활동/idle 추적 (외부 상태).

레거시 ActivityManager가 프로세스 메모리에 갖고 있던 상태 — 프로세스 간 공유가
되도록 외부(Redis)로 뺀다.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class ActivityTracker(Protocol):
    async def touch(self, room_id: UUID) -> None:
        """방에 활동이 있었음을 기록(마지막 활동 시각 갱신)."""
        ...

    async def is_idle(self, room_id: UUID, threshold_seconds: float) -> bool: ...

    async def seconds_since_last(self, room_id: UUID) -> float | None:
        """마지막 활동 이후 경과 초. 기록이 없으면 None."""
        ...
