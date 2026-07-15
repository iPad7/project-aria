"""chat 도메인 모델.

이번 슬라이스에서 메시지는 휘발성이다(히스토리 영속화는 후속). room_id는 라이브
방/세션을, persona_id는 그 방에서 말하는 스트리머를 가리키는 불투명 식별자다.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from aria.common.domain import Entity


class ChatMessage(Entity):
    room_id: UUID
    author_id: UUID
    text: str = Field(min_length=1, max_length=500)
