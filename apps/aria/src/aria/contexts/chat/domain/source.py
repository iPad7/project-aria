"""응답 트리거 소스와 우선순위.

AI 스트리머는 한 번에 하나의 응답만 만든다(단일-플라이트). 여러 트리거가 경합하면
우선순위가 높은 쪽이 이긴다. 레거시 ResponseManager의 우선순위 표를 그대로 옮겼다:
superchat(3) > chat(2) > story(1) = idle(1) > system(0).
"""

from __future__ import annotations

from enum import Enum


class ChatSource(Enum):
    SUPERCHAT = "superchat"
    CHAT = "chat"
    STORY = "story"
    IDLE = "idle"
    SYSTEM = "system"


_PRIORITY: dict[ChatSource, int] = {
    ChatSource.SUPERCHAT: 3,
    ChatSource.CHAT: 2,
    ChatSource.STORY: 1,
    ChatSource.IDLE: 1,
    ChatSource.SYSTEM: 0,
}


def priority_of(source: ChatSource) -> int:
    return _PRIORITY[source]
