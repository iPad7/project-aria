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

# 프로필이 없는 페르소나의 폴백. 기존 페르소나에는 말투가 없으므로 여기서 거부하면
# 전부 죽는다.
#
# **도메인을 말하지 않는다.** 전에는 "연애 상담을 해 주는 AI 페르소나"였는데, 그러면
# 인격도 주제도 설정되지 않은 방송이 조용히 상담사가 된다. 무엇을 하는 방송인지는
# 방이 정하고(`Room.topic`), 여기는 그것도 없을 때의 최소한이다(#75).
DEFAULT_SYSTEM = (
    "너는 시청자와 실시간으로 이야기하는 AI 스트리머다. "
    "시청자의 말에 성의 있게, 한국어로 답한다."
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


def _lines(profile: PersonaProfile, topic: str) -> list[str]:
    out = [f"너는 '{profile.name}'이라는 이름의 AI 스트리머다."]
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

    out.extend(_compass_lines(profile))
    out.extend(_broadcast_lines(topic))

    out.append("한국어로 답한다.")
    return out


def _broadcast_lines(topic: str) -> list[str]:
    """이번 방송의 맥락 — **인격과 한 문단에 섞지 않는다.**

    앞의 것들("너는 누구인가")과 이것("오늘 무엇을 하는가")은 성격이 다르다. 섞어
    한 덩어리로 주면 모델이 주제를 인격의 일부로 읽고, 그러면 페르소나를 바꾸지 않는
    한 주제를 못 바꾸는 상태로 되돌아간다 — 정확히 이번 단위가 없애려는 것이다.
    나침반을 가치관과 분리한 것과 같은 이유이고, 학습 데이터에서 페르소나와 도메인을
    교차시키려면 프롬프트에서도 둘이 구분돼 보여야 한다(`docs/persona-modeling.md`).
    """
    if not topic:
        # 주제 없는 방송은 정상이다 — 말투·나침반과 같은 방침으로, 없으면 안 넣는다.
        return []
    return ["[이번 방송]", f"주제: {topic}"]


def _compass_lines(profile: PersonaProfile) -> list[str]:
    """도덕 나침반 — **가치관과 한 문단에 섞지 않는다.**

    가치관은 "무엇을 중시하는가"이고 나침반은 "그것들이 부딪혔을 때 어느 쪽"이다.
    한 덩어리로 주면 모델이 나침반을 가치 목록의 연장으로 읽어 둘 다 흐려진다.
    별도 문단의 머리말이 그 차이를 명시한다.
    """
    if not profile.moral_standard:
        # 나침반의 앵커는 판단 기준이다. 그것 없이 나머지만 있으면 문단의 머리말이
        # 가리킬 것이 없어, 없는 것으로 본다(`has_compass`도 같은 기준).
        return []

    out = ["판단이 필요할 때 따르는 기준:"]
    out.append(f"- 옳고 그름은 이렇게 가른다: {profile.moral_standard}")
    if profile.rule_adherence:
        out.append(f"- 원칙과 사정이 부딪히면: {profile.rule_adherence}")
    if profile.fairness:
        out.append(f"- 공정하다는 것은: {profile.fairness}")
    return out


def system_message(profile: PersonaProfile | None, topic: str = "") -> Message:
    """프로필과 이번 방송의 주제를 시스템 메시지로.

    `None`(그런 페르소나가 없음)과 `has_voice()` False(말투 미설정)를 같게 다룬다 —
    둘 다 "이 페르소나답게 말할 재료가 없다"이고, 방송을 멈출 이유는 아니다.

    **주제만 있고 인격이 없는 경우는 폴백으로 간다.** 주제 한 줄로 인격을 대신할 수는
    없고, 그렇게 하면 "주제 = 인격"이라는 등식을 프롬프트가 다시 만든다.
    """
    if profile is None or not profile.has_voice():
        return Message(role="system", content=DEFAULT_SYSTEM)
    return Message(role="system", content="\n".join(_lines(profile, topic)))
