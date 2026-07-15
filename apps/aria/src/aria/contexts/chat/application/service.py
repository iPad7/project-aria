"""chat 조율 유스케이스 (async).

레거시가 ActivityManager+ResponseManager+Responder로 흩어 두었던 흐름을 하나의
조율 루프로 모은다: 활동 기록 → 응답 슬롯 확보(우선순위) → 생성(앱/추론 경계) → 슬롯 반납.
LLM 호출은 PersonaLLMPort 뒤에 있어 app은 sLLM인지 OpenAI인지 모른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.port.out.coordinator import ResponseCoordinator
from aria.contexts.chat.application.port.out.llm import Message, PersonaLLMPort
from aria.contexts.chat.domain.message import ChatMessage
from aria.contexts.chat.domain.source import ChatSource


@dataclass(frozen=True)
class ChatReply:
    text: str
    model_version: str | None  # opaque telemetry; 로깅만, 분기 금지


@dataclass(frozen=True)
class MessageOutcome:
    accepted: bool
    reply: ChatReply | None  # AI가 더 높은/같은 우선순위로 바쁘면 None


class ChatOrchestrationService:
    def __init__(
        self,
        activity: ActivityTracker,
        coordinator: ResponseCoordinator,
        llm: PersonaLLMPort,
    ) -> None:
        self._activity = activity
        self._coordinator = coordinator
        self._llm = llm

    async def handle_user_message(
        self, room_id: UUID, persona_id: UUID, author_id: UUID, text: str
    ) -> MessageOutcome:
        # 도메인 불변식 검증(빈 텍스트·길이 등)은 생성 시점에 걸린다.
        message = ChatMessage(room_id=room_id, author_id=author_id, text=text)
        await self._activity.touch(room_id)

        slot = await self._coordinator.try_acquire(room_id, ChatSource.CHAT)
        if slot is None:
            # 메시지는 받되 지금은 응답하지 않는다(더 높은/같은 소스가 응답 중).
            return MessageOutcome(accepted=True, reply=None)

        try:
            result = await self._llm.generate(
                str(persona_id),
                [Message(role="user", content=message.text)],
            )
            reply = ChatReply(text=result.text, model_version=result.model_version)
            return MessageOutcome(accepted=True, reply=reply)
        finally:
            await self._coordinator.release(room_id, slot)
