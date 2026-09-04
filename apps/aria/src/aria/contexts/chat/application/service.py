"""chat 조율 유스케이스 — 요청 경로 (async).

C-4-1에서 생성이 워커로 빠지면서 이 서비스가 하는 일이 짧아졌다: 활동을 기록하고,
방에 **표시할 것을 발행하고**, 즉시 끝난다.

**채팅과 후원의 경로가 다르다.** 후원은 곧바로 생성을 요청한다 — 돈을 냈으니 반드시
답해야 하고, 우선순위도 가장 높다. 반면 일반 채팅은 **후보로 쌓기만** 하고, 진행
워커가 틱마다 그중 하나를 골라 답한다(FR-GEN-1·2). 실제 생성과 우선순위 조율은
`generation.py`의 `ResponseGenerationService`(워커)가 한다.

**표시 발행이 여기 있는 이유.** 전에는 WS 라우터가 메시지 프레임을 발행했다. 그 결과
HTTP로 보낸 메시지는 방에 나타나지 않았고(전송마다 동작이 갈렸다), 후원에서는 더
나빠질 수 있었다 — 워커가 감사 응답을 먼저 발행해 "고맙습니다"가 후원 표시보다 앞서
나가는 순서 뒤집힘이 가능했다. 발행을 유스케이스로 끌어올려 순서를 한 곳에서 못박는다.

후원(FR-PAY-3)은 크레딧 차감을 `SuperchatPort`로 wallet에 맡긴다 — 컨텍스트끼리 직접
부르지 않으므로 계약이 common에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aria.common.superchat import SuperchatPort
from aria.contexts.chat.application.generation import GenerationRequestPublisher
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.port.out.broadcast import RoomBroadcaster
from aria.contexts.chat.application.port.out.candidates import CandidateBuffer
from aria.contexts.chat.domain.message import ChatMessage
from aria.contexts.chat.domain.source import ChatSource
from aria.contexts.chat.domain.topic import Candidate


@dataclass(frozen=True)
class MessageOutcome:
    """메시지 접수 결과.

    응답은 여기 없다. 메시지는 **후보로 쌓이고**, 진행 워커가 틱마다 그중 하나를
    골라 답한다(FR-GEN-1·2). 그래서 `accepted`는 "받았다"이지 "답한다"가 아니다 —
    실제 방송에서도 스트리머가 모든 댓글에 답하지는 않는다.
    """

    accepted: bool


@dataclass(frozen=True)
class SuperchatOutcome:
    """후원 결과 — 차감은 동기라 즉시 확정된 값을 돌려준다.

    감사 응답은 여기 없다. 슬롯을 못 잡아 응답이 아예 안 생길 수도 있는데, 그래도
    **후원은 성립한 것이다** — 차감·기록은 이미 끝났다. 차감이 실패했다면 애초에
    예외로 나가고 여기 오지 않는다.
    """

    donation_id: UUID
    balance_after: int


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
        broadcaster: RoomBroadcaster,
        generation: GenerationRequestPublisher,
        superchat: SuperchatPort,
        candidates: CandidateBuffer,
    ) -> None:
        self._activity = activity
        self._broadcaster = broadcaster
        self._generation = generation
        self._superchat = superchat
        self._candidates = candidates

    async def handle_user_message(
        self, room_id: UUID, persona_id: UUID, author_id: UUID, text: str
    ) -> MessageOutcome:
        # 도메인 불변식 검증(빈 텍스트·길이 등)은 생성 시점에 걸린다.
        message = ChatMessage(room_id=room_id, author_id=author_id, text=text)
        await self._activity.touch(room_id)

        await self._broadcaster.publish(
            room_id,
            {
                "type": "message",
                "room_id": str(room_id),
                "author_id": str(author_id),
                "text": message.text,
            },
        )
        # **생성 요청을 여기서 내지 않는다.** 후보로 쌓아 두면 진행 워커가 틱마다
        # 그중 하나를 골라 답한다(FR-GEN-1·2).
        #
        # 전에는 메시지마다 요청이 나가고 슬롯을 못 잡은 것은 조용히 버려졌다 —
        # 즉 "선별"이 Redis 락 경쟁이었다. 초당 수십 개가 들어오는 방송에서는
        # 아무 의미가 없다.
        await self._candidates.add(
            room_id,
            Candidate(message_id=message.id, author_id=author_id, text=message.text),
        )
        return MessageOutcome(accepted=True)

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
        """후원을 받고 감사 응답을 예약한다.

        **순서가 계약이다**(`docs/events.md`). ① 차감이 먼저다 — 실패하면
        `InsufficientCreditError`가 그대로 올라가고 방송에는 아무 것도 나가지 않는다.
        ② 차감이 성공하면 후원 표시를 **감사 응답과 무관하게 항상** 발행한다. ③ 마지막에
        생성을 큐에 맡긴다. 셋을 이 순서로 여기에 모아 둬야 후원 표시보다 감사 응답이
        먼저 나가는 뒤집힘이 생기지 않는다.
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

        await self._broadcaster.publish(
            room_id,
            {
                "type": "superchat",
                "room_id": str(room_id),
                "persona_id": str(persona_id),
                "donor_id": str(donor_id),
                "donation_id": str(receipt.donation_id),
                "amount": amount,
                "message": message,
            },
        )
        await self._generation.request(
            room_id, persona_id, ChatSource.SUPERCHAT, _thanks_prompt(amount, message)
        )
        return SuperchatOutcome(
            donation_id=receipt.donation_id, balance_after=receipt.balance_after
        )
