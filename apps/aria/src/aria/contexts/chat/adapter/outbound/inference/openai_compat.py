"""OpenAI 호환 API로 PersonaLLMPort를 구현하는 어댑터.

vLLM은 OpenAI 호환 엔드포인트(``/v1/chat/completions``)를 서빙한다. 그래서 이 한
어댑터가 외부 OpenAI와 자체 vLLM 서빙을 모두 커버한다 — ``base_url``만 다르다.
app은 뒤가 GPT든 SFT한 sLLM이든 구분하지 못한다(포트의 요점).

클라이언트를 생성자로 주입받아 네트워크 없이 단위 테스트할 수 있다.

페르소나별 시스템 프롬프트 해석은 아직 없다 — 페르소나 데이터(설명·성격)는 persona
컨텍스트 소유라 chat이 직접 import할 수 없다. 지금은 공통 상담 프롬프트를 주입하고,
페르소나별 해석은 별도 포트/이벤트로 뒤에 붙인다.
"""

from __future__ import annotations

from collections.abc import Sequence

from openai import AsyncOpenAI

from aria.contexts.chat.application.port.out.llm import (
    GenParams,
    LLMResult,
    Message,
)

_DEFAULT_SYSTEM = (
    "너는 연애 상담을 해 주는 AI 페르소나다. 시청자의 사연에 공감하며 "
    "따뜻하고 구체적으로, 한국어로 답한다."
)


class OpenAICompatLLM:
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        system_prompt: str = _DEFAULT_SYSTEM,
    ) -> None:
        self._client = client
        self._model = model
        self._system_prompt = system_prompt

    async def generate(
        self,
        persona_id: str,  # A-1에선 미사용 — 페르소나별 프롬프트 해석은 후속
        messages: Sequence[Message],
        params: GenParams | None = None,
    ) -> LLMResult:
        params = params or GenParams()
        chat_messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt}
        ]
        chat_messages.extend({"role": m.role, "content": m.content} for m in messages)

        completion = await self._client.chat.completions.create(
            model=self._model,
            messages=chat_messages,  # type: ignore[arg-type]
            max_tokens=params.max_tokens,
            temperature=params.temperature,
        )
        choice = completion.choices[0]
        return LLMResult(
            text=(choice.message.content or "").strip(),
            model_version=completion.model,  # 불투명 텔레메트리 — 분기 금지
        )
