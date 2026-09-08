"""chat 도메인 모델 — 방에서 오간 것 한 줄.

**전에는 휘발이었다.** 시청자 채팅은 Redis 후보 버퍼(TTL)에, 응답은 pub/sub에만
있었고 어디에도 남지 않았다. 그래서 새로고침하면 방이 통째로 비었고, 학습에 쓸 쌍도
한 줄이 없었다(#73). 이 파일의 이전 판 docstring이 "히스토리 영속화는 후속"이라고
적어 둔 그 후속이다.

**세 가지를 한 종류로 둔다.** 시청자 채팅·후원·페르소나 응답은 화면에서도 하나의
타임라인이고, 학습 쌍도 그 순서에서 나온다. 종류별로 나누면 "방에서 무슨 일이
있었나"라는 유일한 주요 질문이 UNION이 된다.

`room_id`·`author_id`·`persona_id`는 다른 컨텍스트를 가리키지만 **불투명 UUID**일
뿐이다 — chat은 identity도 persona도 import하지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import Field, model_validator

from aria.common.domain import Entity
from aria.contexts.chat.domain.source import ChatSource

# 시청자 입력의 상한. 응답은 LLM이 만들므로 더 길 수 있어 따로 둔다.
MAX_VIEWER_TEXT = 500
MAX_TEXT = 2000


class MessageKind(Enum):
    CHAT = "chat"
    SUPERCHAT = "superchat"
    REPLY = "reply"


class RoomMessage(Entity):
    """타임라인 한 줄.

    필드가 종류마다 비는 것은 의도다 — `amount`는 후원에만, `model_version`은 응답에만
    있다. 종류별 테이블로 쪼개는 대신 판별자를 두고, **어긋난 조합은 아래 검증이 막는다.**
    """

    room_id: UUID
    kind: MessageKind
    # 후원은 메시지 없이도 성립하므로 빈 문자열을 허용한다. 나머지는 아래에서 막는다.
    text: str = Field(default="", max_length=MAX_TEXT)
    # 시청자(채팅·후원). 응답이면 없다.
    author_id: UUID | None = None
    # 말한 페르소나. 응답·후원(어느 방송에 후원했나)에 있다.
    persona_id: UUID | None = None
    # 응답이 무엇에 촉발됐나 — chat/superchat/story/idle.
    source: ChatSource | None = None
    model_version: str | None = Field(default=None, max_length=100)
    amount: int | None = Field(default=None, gt=0)
    # **학습 쌍의 연결선.** 이 응답이 답한 시청자 메시지(#63의 선별 결과). 자율발화·
    # 사연 낭독은 답할 대상이 없으므로 비어 있다.
    replied_to: UUID | None = None
    # 시각을 도메인에 둔다 — 기록에서 "언제"는 부가 정보가 아니라 항목 자체의 일부다
    # (`CreditTransaction`과 같은 이유). 저장된 값은 DB의 server_default가 이기고,
    # 여기 기본값은 아직 저장되지 않은 객체를 위한 것이다.
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _fields_match_kind(self) -> RoomMessage:
        if self.kind is not MessageKind.SUPERCHAT and not self.text.strip():
            raise ValueError(f"{self.kind.value}는 빈 텍스트일 수 없습니다")
        if self.kind is MessageKind.CHAT and len(self.text) > MAX_VIEWER_TEXT:
            raise ValueError(f"시청자 메시지는 {MAX_VIEWER_TEXT}자를 넘을 수 없습니다")
        if self.kind is MessageKind.REPLY and self.author_id is not None:
            # 페르소나 응답에 시청자를 저자로 달면 "누가 말했나"가 무너진다.
            raise ValueError("응답에는 author_id가 없습니다")
        if self.kind is not MessageKind.REPLY and self.replied_to is not None:
            raise ValueError("replied_to는 응답에만 있습니다")
        if self.kind is not MessageKind.SUPERCHAT and self.amount is not None:
            raise ValueError("amount는 후원에만 있습니다")
        return self

    @classmethod
    def from_viewer(cls, room_id: UUID, author_id: UUID, text: str) -> RoomMessage:
        return cls(
            room_id=room_id, kind=MessageKind.CHAT, author_id=author_id, text=text
        )

    @classmethod
    def from_superchat(
        cls,
        room_id: UUID,
        persona_id: UUID,
        donor_id: UUID,
        amount: int,
        message: str | None,
    ) -> RoomMessage:
        return cls(
            room_id=room_id,
            kind=MessageKind.SUPERCHAT,
            author_id=donor_id,
            persona_id=persona_id,
            amount=amount,
            text=message or "",
        )

    @classmethod
    def from_persona(
        cls,
        room_id: UUID,
        persona_id: UUID,
        source: ChatSource,
        text: str,
        *,
        model_version: str | None = None,
        replied_to: UUID | None = None,
    ) -> RoomMessage:
        return cls(
            room_id=room_id,
            kind=MessageKind.REPLY,
            persona_id=persona_id,
            source=source,
            text=text,
            model_version=model_version,
            replied_to=replied_to,
        )
