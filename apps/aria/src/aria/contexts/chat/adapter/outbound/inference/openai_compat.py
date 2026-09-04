"""OpenAI 호환 API로 PersonaLLMPort를 구현하는 어댑터.

vLLM은 OpenAI 호환 엔드포인트(``/v1/chat/completions``)를 서빙한다. 그래서 이 한
어댑터가 외부 OpenAI와 자체 vLLM 서빙을 모두 커버한다 — ``base_url``만 다르다.
app은 뒤가 GPT든 SFT한 sLLM이든 구분하지 못한다(포트의 요점).

클라이언트를 생성자로 주입받아 네트워크 없이 단위 테스트할 수 있다.

**시스템 프롬프트를 여기서 만들지 않는다.** 페르소나 해석은 application이 하고
(`chat/application/persona_prompt.py`), 결과를 `messages`의 첫 원소로 넘겨준다.
어댑터가 또 주입하면 시스템 메시지가 둘이 되고, 무엇보다 어댑터가 페르소나를
조회하게 되면 "앱은 persona_id만 넘기고 모델 세부를 모른다"는 경계가 무너진다.
"""

from __future__ import annotations

from collections.abc import Sequence

from openai import AsyncOpenAI

from aria.contexts.chat.application.port.out.llm import (
    GenParams,
    LLMResult,
    Message,
)


class OpenAICompatLLM:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def generate(
        self,
        # 어댑터는 이 id를 쓰지 않는다. 인격은 이미 messages의 시스템 메시지에 들어
        # 있고, 운영 경로에서 이 id가 쓰이는 곳은 포트 **뒤**(멀티-LoRA 선택)다.
        persona_id: str,
        messages: Sequence[Message],
        params: GenParams | None = None,
    ) -> LLMResult:
        params = params or GenParams()
        chat_messages = [{"role": m.role, "content": m.content} for m in messages]

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
