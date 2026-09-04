"""페르소나 프로필 → 시스템 메시지.

이 단위의 핵심 로직이다. `persona_id`가 인격이 되는 지점이 여기 하나뿐이라,
"페르소나가 왜 이렇게 말했나"를 추적할 때 볼 곳도 여기 하나다.

**왜 어댑터가 아니라 application인가.** 프롬프트를 어댑터(`OpenAICompatLLM`)에서
만들면 앱/추론 경계에 구멍이 생긴다 — 어댑터가 페르소나를 조회하게 되고, "앱은
`persona_id`만 넘기고 모델 세부를 모른다"는 계약이 한쪽에서 무너진다. 여기서 만들면
결과물이 그냥 `Message` 하나라, 포트 뒤가 GPT든 LoRA든 상관없다.

**왜 요청 페이로드에 싣지 않는가.** 생성 요청은 Kafka를 거치므로, 프로필을 실어
보내면 큐에 남아 있던 옛 요청이 옛 말투로 답한다. 소비 시점에 읽는 편이 맞다.
"""

from __future__ import annotations

from aria.common.persona_profile import PersonaProfile
from aria.contexts.chat.application.port.out.llm import Message

# 프로필이 없는 페르소나의 폴백. 이 서비스가 무엇을 하는 곳인지만 말해 준다.
# 기존 페르소나에는 말투가 없으므로 여기서 거부하면 전부 죽는다.
DEFAULT_SYSTEM = (
    "너는 연애 상담을 해 주는 AI 페르소나다. 시청자의 사연에 공감하며 "
    "따뜻하고 구체적으로, 한국어로 답한다."
)

# directness 1~5를 문장으로. 숫자를 그대로 프롬프트에 넣으면 모델이 그 척도가
# 무엇인지 모른다 — "3"은 아무 뜻도 없다.
_DIRECTNESS: dict[int, str] = {
    1: "돌려 말하고, 단정적인 표현을 피한다",
    2: "조심스럽게 의견을 낸다",
    3: "필요한 만큼 솔직하게 말한다",
    4: "에두르지 않고 분명하게 말한다",
    5: "매우 직설적으로, 듣기 불편해도 할 말은 한다",
}


def _lines(profile: PersonaProfile) -> list[str]:
    out = [f"너는 '{profile.name}'이라는 이름의 AI 연애상담 스트리머다."]
    if profile.description:
        out.append(profile.description)

    if profile.tone:
        out.append(f"말투: {profile.tone}")
    if profile.sentence_length:
        out.append(f"문장 길이: {profile.sentence_length}")
    if profile.directness is not None:
        out.append(f"솔직함: {_DIRECTNESS[profile.directness]}")
    if profile.empathy_expression:
        out.append(f"공감 표현: {profile.empathy_expression}")
    if profile.question_style:
        out.append(f"질문 방식: {profile.question_style}")

    if profile.core_values:
        # 순서가 곧 우선순위다 — 번호를 붙여야 모델이 그것을 읽는다.
        ranked = " > ".join(
            f"{i}. {name}" for i, name in enumerate(profile.core_values, start=1)
        )
        out.append(f"중시하는 가치(앞설수록 우선): {ranked}")
        out.append("가치가 서로 부딪히면 앞선 가치를 따른다.")

    out.append("한국어로 답한다.")
    return out


def system_message(profile: PersonaProfile | None) -> Message:
    """프로필을 시스템 메시지로. 없거나 비어 있으면 공통 프롬프트.

    `None`(그런 페르소나가 없음)과 `has_voice()` False(말투 미설정)를 같게 다룬다 —
    둘 다 "이 페르소나답게 말할 재료가 없다"이고, 방송을 멈출 이유는 아니다.
    """
    if profile is None or not profile.has_voice():
        return Message(role="system", content=DEFAULT_SYSTEM)
    return Message(role="system", content="\n".join(_lines(profile)))
