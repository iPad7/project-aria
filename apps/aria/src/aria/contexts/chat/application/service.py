"""chat 조율 유스케이스 (async).

레거시가 ActivityManager+ResponseManager+Responder로 흩어 두었던 흐름을 하나의
조율 루프로 모은다: 활동 기록 → 응답 슬롯 확보(우선순위) → 생성(앱/추론 경계) → 슬롯 반납.
LLM 호출은 PersonaLLMPort 뒤에 있어 app은 sLLM인지 OpenAI인지 모른다.

후원(FR-PAY-3)은 크레딧 차감을 `SuperchatPort`로 wallet에 맡기고, 감사 응답(FR-GEN-6)만
여기서 조율한다 — 컨텍스트끼리 직접 부르지 않으므로 계약이 common에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aria.common.superchat import SuperchatPort
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.port.out.coordinator import (
    ResponseCoordinator,
    ResponseSlot,
)
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


@dataclass(frozen=True)
class SuperchatOutcome:
    """후원 결과.

    `reply`가 None이어도 **후원은 성립한 것이다** — 차감·기록은 이미 끝났고 감사 응답만
    생기지 않았다는 뜻이다. 차감이 실패했다면 애초에 예외로 나가고 여기 오지 않는다.
    """

    donation_id: UUID
    balance_after: int
    reply: ChatReply | None


def _thanks_prompt(amount: int, message: str | None) -> str:
    """감사 응답을 유도하는 입력.

    페르소나별 시스템 프롬프트 해석은 #22의 몫이라 여기서는 사실만 전달한다 —
    얼마를 후원했고 무슨 말을 남겼는지. 말투는 포트 뒤에서 결정된다.
    """
    if message:
        return f"방금 {amount} 크레딧을 후원하며 이렇게 말했습니다: {message}"
    return f"방금 {amount} 크레딧을 후원했습니다."


class ChatOrchestrationService:
    def __init__(
        self,
        activity: ActivityTracker,
        coordinator: ResponseCoordinator,
        llm: PersonaLLMPort,
        superchat: SuperchatPort,
    ) -> None:
        self._activity = activity
        self._coordinator = coordinator
        self._llm = llm
        self._superchat = superchat

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

        reply = await self._generate(room_id, persona_id, message.text, slot)
        return MessageOutcome(accepted=True, reply=reply)

    async def handle_superchat(
        self,
        room_id: UUID,
        persona_id: UUID,
        donor_id: UUID,
        amount: int,
        *,
        message: str | None = None,
        idempotency_key: str | None = None,
    ) -> SuperchatOutcome:
        """후원을 받고 감사 응답을 만든다.

        **순서가 계약이다.** 차감이 먼저다 — 실패하면 `InsufficientCreditError`가 그대로
        올라가고 방송에는 아무 것도 나가지 않는다. 차감이 성공한 뒤에는 응답 슬롯을 못
        잡아도 후원 자체는 성립한다. 돈을 받아 두고 표시를 못 하는 일이 없도록, 후원
        표시는 이 결과를 받는 어댑터가 `reply`와 무관하게 발행한다.
        """
        receipt = await self._superchat.charge(
            donor_id,
            persona_id,
            amount,
            room_id=room_id,
            message=message,
            idempotency_key=idempotency_key,
        )
        await self._activity.touch(room_id)

        slot = await self._coordinator.try_acquire(room_id, ChatSource.SUPERCHAT)
        reply = (
            None
            if slot is None
            else await self._generate(
                room_id, persona_id, _thanks_prompt(amount, message), slot
            )
        )
        return SuperchatOutcome(
            donation_id=receipt.donation_id,
            balance_after=receipt.balance_after,
            reply=reply,
        )

    async def _generate(
        self, room_id: UUID, persona_id: UUID, prompt: str, slot: ResponseSlot
    ) -> ChatReply | None:
        """슬롯을 쥔 채 응답을 만든다. 그 사이 선점당했으면 결과를 버린다.

        생성은 외부 호출이라 중간에 취소할 수 없다. 그래서 취소 대신 **내보내기 직전에
        아직 내 슬롯인지 확인**한다 — 이게 없으면 밀려난 응답이 그대로 발행돼 우선순위가
        무의미해진다.
        """
        try:
            result = await self._llm.generate(
                str(persona_id), [Message(role="user", content=prompt)]
            )
            if not await self._coordinator.still_holds(room_id, slot):
                return None  # 선점당했다 — 만든 응답을 버린다
            return ChatReply(text=result.text, model_version=result.model_version)
        finally:
            await self._coordinator.release(room_id, slot)
