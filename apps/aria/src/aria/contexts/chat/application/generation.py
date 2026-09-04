"""응답 생성 유스케이스 (generation-worker 쪽).

C-4-1에서 생성을 요청 경로 밖으로 꺼내면서 조율이 둘로 갈라졌다:

- `service.py`(api) — 활동 기록 · 화면 표시 발행 · **생성 요청 발행**. 즉시 끝난다.
- 여기(worker) — 요청을 받아 **슬롯을 잡고** 생성하고 응답을 발행한다.

**슬롯을 소비 시점에 잡는 이유.** 슬롯의 의미는 "지금 이 방에서 생성 중인 자"다.
발행 시점에 잡으면 큐에서 기다리는 동안 슬롯을 쥐게 되어, 대기 중인 요청이 실제
생성을 막는다. 그래서 `try_acquire`/`still_holds`/`release`가 전부 이쪽에 있다.

**우선순위는 토픽 drain 순서가 아니라 코디네이터가 지킨다.** `docs/events.md`는 원래
"워커가 superchat 토픽을 먼저 drain하고 없을 때만 normal을 처리"하는 Kafka 표준
패턴을 적어 두었지만, 그건 우선순위 장치가 큐 순서밖에 없을 때의 이야기다. 우리는
`ResponseCoordinator`가 이미 **진행 중인 생성을 선점**할 수 있다 — drain 순서보다
강하다. 큐에서 아무리 잘 골라 봐야 이미 돌고 있는 채팅 응답은 못 멈추지만, 선점은
멈춘다. 그래서 두 토픽을 나란히 구독하고 순서는 코디네이터에 맡긴다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from aria.common.eventbus import Event, EventBusPort
from aria.common.ids import new_id
from aria.common.persona_profile import PersonaProfilePort
from aria.common.tracing import Observation, TracingPort
from aria.contexts.chat.application.persona_prompt import system_message
from aria.contexts.chat.application.port.out.broadcast import RoomBroadcaster
from aria.contexts.chat.application.port.out.coordinator import ResponseCoordinator
from aria.contexts.chat.application.port.out.llm import Message, PersonaLLMPort
from aria.contexts.chat.domain.source import ChatSource

# durable 토픽. 슈퍼챗이 별도 토픽인 것은 `docs/events.md`의 결정 그대로다 — 다만
# 우선순위를 지키는 것은 토픽 분리가 아니라 코디네이터다(위 docstring).
logger = logging.getLogger(__name__)

RESPONSE_REQUESTED = "aria.chat.response-requested"
SUPERCHAT_REQUESTED = "aria.chat.superchat-requested"

# 페이로드 스키마 버전. 소비자가 모르는 버전을 만나면 추측하지 않고 DLQ로 보낸다 —
# 필드가 바뀐 메시지를 옛 코드가 반쯤 읽어 이상한 응답을 내보내는 것이 최악이다.
# 지금 넣는 비용이 거의 0이라 소비자가 우리 자신뿐일 때 미리 넣어 둔다.
SCHEMA_VERSION = 1


class UnsupportedSchemaVersion(ValueError):
    """모르는 페이로드 버전. 잡아서 DLQ로 보내라는 신호다."""


@dataclass(frozen=True)
class GenerationRequest:
    """생성 요청 — api가 발행하고 worker가 받는다.

    **직렬화를 DTO 자신이 안다.** 보통은 어댑터의 일이지만, 이 페이로드는 발행 쪽
    어댑터와 소비 쪽 어댑터 **양쪽**이 똑같이 알아야 한다. 어느 한쪽 어댑터에 두면
    다른 쪽이 그것을 import하게 되고, 둘 사이에 모듈을 하나 더 만드는 것보다
    dict 표현을 DTO에 붙이는 편이 짧다. Kafka를 아는 것은 아니다 — 평범한 dict다.
    """

    msg_id: UUID
    room_id: UUID
    persona_id: UUID
    source: ChatSource
    prompt: str
    requested_at: datetime
    # 선별 근거(FR-GEN-2). 채팅 응답일 때만 채워지고, 트레이스에 실려 "후보 몇 개
    # 중 왜 저걸 골랐나"에 답한다. 없어도 생성은 그대로 돈다.
    selection: dict[str, Any] | None = None

    @classmethod
    def create(
        cls,
        room_id: UUID,
        persona_id: UUID,
        source: ChatSource,
        prompt: str,
        selection: dict[str, Any] | None = None,
    ) -> GenerationRequest:
        return cls(
            selection=selection,
            # 중복 소비를 흡수하는 멱등키. 워커가 `ProcessedRegistry`로 이걸 claim한다.
            msg_id=new_id(),
            room_id=room_id,
            persona_id=persona_id,
            source=source,
            prompt=prompt,
            requested_at=datetime.now(UTC),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "v": SCHEMA_VERSION,
            "msg_id": str(self.msg_id),
            "room_id": str(self.room_id),
            "persona_id": str(self.persona_id),
            "source": self.source.value,
            "prompt": self.prompt,
            "requested_at": self.requested_at.isoformat(),
            "selection": self.selection,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> GenerationRequest:
        # 버전이 없으면 1로 본다 — C-4-2 이전에 발행돼 큐에 남아 있던 메시지.
        version = payload.get("v", 1)
        if version != SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(
                f"모르는 페이로드 버전 {version} (지원: {SCHEMA_VERSION})"
            )
        return cls(
            msg_id=UUID(payload["msg_id"]),
            room_id=UUID(payload["room_id"]),
            persona_id=UUID(payload["persona_id"]),
            source=ChatSource(payload["source"]),
            prompt=payload["prompt"],
            requested_at=datetime.fromisoformat(payload["requested_at"]),
            # C-4-2 이전 페이로드에는 없다 — 없으면 그냥 근거가 없는 것이다.
            selection=payload.get("selection"),
        )

    def to_event(self) -> Event:
        # 키가 room_id라 같은 방의 요청은 같은 파티션 → 순서가 보장된다.
        stream = (
            SUPERCHAT_REQUESTED
            if self.source is ChatSource.SUPERCHAT
            else RESPONSE_REQUESTED
        )
        return Event(stream=stream, key=str(self.room_id), payload=self.to_payload())


class GenerationRequestPublisher:
    """요청 경로가 생성을 맡기는 쪽. api가 쓴다."""

    def __init__(self, events: EventBusPort) -> None:
        self._events = events

    async def request(
        self,
        room_id: UUID,
        persona_id: UUID,
        source: ChatSource,
        prompt: str,
        selection: dict[str, Any] | None = None,
    ) -> GenerationRequest:
        request = GenerationRequest.create(
            room_id, persona_id, source, prompt, selection
        )
        await self._events.publish(request.to_event())
        return request


class ResponseGenerationService:
    """워커의 유스케이스. 요청 하나를 받아 응답 하나를 방송한다."""

    def __init__(
        self,
        coordinator: ResponseCoordinator,
        llm: PersonaLLMPort,
        broadcaster: RoomBroadcaster,
        profiles: PersonaProfilePort,
        tracing: TracingPort,
    ) -> None:
        self._coordinator = coordinator
        self._llm = llm
        self._broadcaster = broadcaster
        self._profiles = profiles
        self._tracing = tracing

    async def handle(self, request: GenerationRequest) -> None:
        # 바깥 span. 어댑터가 못 보는 맥락(어느 방·무엇이 촉발·중복인지)이 여기 있고,
        # LLM 호출 span은 이 안에 중첩된다. `docs/architecture.md` 참조.
        with self._tracing.observe(
            f"response:{request.source.value}",
            input=request.prompt,
            metadata={
                "room_id": str(request.room_id),
                "persona_id": str(request.persona_id),
                "source": request.source.value,
                "msg_id": str(request.msg_id),
                # 선별 근거. 채팅 응답일 때만 있다.
                **(request.selection or {}),
            },
        ) as trace:
            await self._handle(request, trace)

    async def _handle(self, request: GenerationRequest, trace: Observation) -> None:
        slot = await self._coordinator.try_acquire(request.room_id, request.source)
        if slot is None:
            # 더 높은/같은 우선순위가 응답 중이다. 이 요청은 조용히 버린다 —
            # 사용자의 메시지·후원 표시는 이미 방송에 나갔고, 없는 것은 응답뿐이다.
            trace.set_metadata({"outcome": "no_slot"})
            return

        try:
            # 인격은 여기서 붙는다. 프로필이 없으면 공통 프롬프트로 폴백하되 그
            # 사실을 남긴다 — 관측성이 붙으면 "몇 %가 프로필 없이 도는가"가 된다.
            profile = await self._profiles.profile_of(request.persona_id)
            has_voice = profile is not None and profile.has_voice()
            # #59에서 로그로만 남기던 것이 이제 트레이스 속성이 된다 — "몇 %가
            # 프로필 없이 도는가"를 셀 수 있다.
            trace.set_metadata({"has_persona_voice": has_voice})
            if not has_voice:
                logger.info(
                    "페르소나 프로필 없음 — 공통 프롬프트로 답한다 persona_id=%s",
                    request.persona_id,
                )
            result = await self._llm.generate(
                str(request.persona_id),
                [
                    system_message(profile),
                    Message(role="user", content=request.prompt),
                ],
            )
            if not await self._coordinator.still_holds(request.room_id, slot):
                # 생성하는 동안 선점당했다 — 만들어 둔 응답을 버린다. 생성은 외부
                # 호출이라 중간에 취소할 수 없으므로, 취소 대신 내보내기 직전에 확인한다.
                #
                # 이 표시가 특히 쓸모 있다: **버려진 생성의 비용**이 보인다.
                trace.set_metadata({"outcome": "preempted"})
                return
            await self._broadcaster.publish(
                request.room_id,
                {
                    "type": "reply",
                    "room_id": str(request.room_id),
                    "persona_id": str(request.persona_id),
                    # 클라이언트가 감사 응답과 일반 응답을 구분하는 근거.
                    "source": request.source.value,
                    "text": result.text,
                    "model_version": result.model_version,
                },
            )
            trace.set_output(result.text)
            trace.set_metadata({"outcome": "published"})
        finally:
            await self._coordinator.release(request.room_id, slot)
