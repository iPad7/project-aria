"""PersonaProfilePort — 페르소나 인격의 컨텍스트 간 계약.

`PersonaLLMPort`는 `persona_id`만 받는다. 그런데 **그 id를 인격으로 해석할 재료는
persona 컨텍스트에 있고**, chat은 persona를 import할 수 없다. 그래서 계약이 커널에
산다 — `StoryFeed`·`Superchat`·`Ranking`·`UserDirectory`에 이은 다섯 번째다.

**앱/추론 경계는 그대로다.** chat은 여전히 모델 버전을 모르고, 여기서 받은 프로필을
`Message(role="system")` 하나로 바꿔 포트에 넘길 뿐이다. 포트 뒤가 GPT든 LoRA를 얹은
sLLM이든 구분하지 못한다.

**LoRA가 붙어도 이 포트는 남는다.** 운영 경로에서 페르소나 구별이 멀티-LoRA로
일어나더라도, LoRA는 **말투**를 잡고 시스템 프롬프트는 **맥락·가치관**을 준다 —
둘은 대체 관계가 아니다. 더 중요하게는, 이 경로로 나온 응답이 나중에 그 LoRA를
학습시킬 데이터가 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class PersonaProfile:
    """페르소나를 말하게 하는 데 필요한 것만.

    **persona의 도메인 객체가 아니다.** 그대로 넘기면 chat이 persona의 타입을 알게
    된다 — `PendingStory`가 `Story`를 대신하는 것과 같은 이유다.
    """

    persona_id: UUID
    name: str
    description: str = ""
    tone: str | None = None
    sentence_length: str | None = None
    question_style: str | None = None
    # 1: 매우 완곡 ~ 5: 매우 직설적. 없으면 None.
    directness: int | None = None
    empathy_expression: str | None = None
    # 우선순위 순. 첫 번째가 1순위다.
    core_values: tuple[str, ...] = field(default_factory=tuple)

    def has_voice(self) -> bool:
        """인격을 말투로 표현할 재료가 있는가.

        False면 이 페르소나는 아직 이름과 설명뿐이다 — 소비자는 공통 프롬프트로
        폴백한다. 프로필이 없다고 생성을 거부하면 기존 페르소나가 전부 죽는다.
        """
        return bool(self.tone or self.core_values)


class PersonaProfilePort(Protocol):
    async def profile_of(self, persona_id: UUID) -> PersonaProfile | None:
        """페르소나의 인격. 그런 페르소나가 없으면 None.

        **없음(None)과 비어 있음(`has_voice()` False)은 다르다.** 앞은 잘못된
        `persona_id`이고, 뒤는 아직 말투를 설정하지 않은 정상 페르소나다.
        """
        ...
