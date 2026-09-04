"""persona 도메인 모델.

Persona는 스트리머(AI 인격)다. `owner_id`는 이를 만든 사용자(identity)를 가리키지만,
persona 컨텍스트는 identity를 import하지 않는다 — owner_id는 그저 불투명한 UUID다.

`Persona`가 "누구인가"라면 `CommunicationStyle`·`CoreValue`는 **"어떻게 말하는가"**다.
이 값들이 `PersonaProfilePort`를 통해 chat으로 건너가 시스템 프롬프트가 된다 —
그전까지 `persona_id`는 포트에 넘겨지기만 하고 아무도 해석하지 않았다.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from aria.common.domain import Entity


class Persona(Entity):
    owner_id: UUID
    name: str = Field(min_length=1, max_length=30)
    tagline: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=2000)
    is_active: bool = True

    def edit(
        self,
        *,
        name: str | None = None,
        tagline: str | None = None,
        description: str | None = None,
    ) -> None:
        """부분 수정. validate_assignment로 각 대입이 즉시 재검증된다."""
        if name is not None:
            self.name = name
        if tagline is not None:
            self.tagline = tagline
        if description is not None:
            self.description = description


class CommunicationStyle(BaseModel):
    """말투 — 페르소나가 **어떻게** 말하는가 (1:1 persona).

    엔티티가 아니다. 식별자가 `persona_id` 자체이고 페르소나 없이 홀로 존재하지
    않는다(`wallet.Wallet`이 `Entity`가 아닌 것과 같은 이유).

    값을 자유 문자열로 두는 이유: 이 값들은 결국 **프롬프트 문장이 된다**. enum으로
    좁히면 표현할 수 있는 인격의 폭이 코드 배포 주기에 묶인다. 다만 `directness`만은
    정도(degree)라 1~5로 못박는다 — "직설적"과 "매우 직설적"을 문자열로 구분하면
    프롬프트가 흔들린다.
    """

    persona_id: UUID
    # 예: "따뜻하고 나긋한", "장난기 있는"
    tone: str = Field(min_length=1, max_length=100)
    # 예: "짧고 간결하게", "길고 자세하게"
    sentence_length: str = Field(default="", max_length=100)
    # 예: "되묻지 않고 조언한다", "질문으로 스스로 답을 찾게 한다"
    question_style: str = Field(default="", max_length=200)
    # 1: 매우 완곡 ~ 5: 매우 직설적
    directness: int = Field(default=3, ge=1, le=5)
    # 예: "적극적으로 공감을 표현한다"
    empathy_expression: str = Field(default="", max_length=200)


class CoreValue(Entity):
    """가치관 어휘. 여러 페르소나가 공유하므로 별도 엔티티다."""

    value_name: str = Field(min_length=1, max_length=50)


class PersonaCoreValue(BaseModel):
    """페르소나가 어떤 가치를 **몇 번째로** 중시하는가.

    우선순위가 있는 M:N이라 연결 자체가 값을 갖는다 — 같은 가치라도 1순위인 페르소나와
    5순위인 페르소나는 다르게 말한다.
    """

    persona_id: UUID
    core_value_id: UUID
    priority: int = Field(ge=1)
