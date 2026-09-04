"""PersonaLLMPort를 계측하는 데코레이터 어댑터.

`FallbackPersonaLLM`과 같은 형태다 — `PersonaLLMPort`를 **구현하면서** 하나를 감싼다.
그래서 application은 계측의 존재를 모른다.

**감싸는 순서가 의미를 만든다.** 합성 루트는 폴백을 계측 **안쪽**에 둔다:

    TracedPersonaLLM(FallbackPersonaLLM(primary, fallback))   ← 호출 1건 = generation 1개
    FallbackPersonaLLM(TracedPersonaLLM(primary), TracedPersonaLLM(fallback))  ← 2개

후자를 택한다. 폴백이 일어나면 generation이 **두 개** 남아 "주 백엔드가 실패해서
폴백했다"는 사실이 트레이스에 그대로 보이기 때문이다. 전자로 감싸면 그게 사라지고
`model_version`으로만 추측하게 된다.
"""

from __future__ import annotations

from collections.abc import Sequence

from aria.common.tracing import TracingPort
from aria.contexts.chat.application.port.out.llm import (
    GenParams,
    LLMResult,
    Message,
    PersonaLLMPort,
)


class TracedPersonaLLM:
    def __init__(
        self, inner: PersonaLLMPort, tracing: TracingPort, *, name: str
    ) -> None:
        self._inner = inner
        self._tracing = tracing
        # 어느 백엔드인지 이름으로 구분한다 — 폴백 시 두 generation이 섞이지 않는다.
        self._name = name

    async def generate(
        self,
        persona_id: str,
        messages: Sequence[Message],
        params: GenParams | None = None,
    ) -> LLMResult:
        params = params or GenParams()
        with self._tracing.observe(
            self._name,
            kind="generation",
            # 프롬프트 전문을 남긴다 — 이게 "페르소나 프롬프트가 먹히나"를 보는 유일한 방법이다.
            input=[{"role": m.role, "content": m.content} for m in messages],
            metadata={
                "persona_id": persona_id,
                "max_tokens": params.max_tokens,
                "temperature": params.temperature,
            },
        ) as observation:
            result = await self._inner.generate(persona_id, messages, params)
            observation.set_output(result.text)
            # 포트 규칙: model_version은 관측용이고 분기 금지. 관측이 바로 여기다.
            observation.set_metadata({"model_version": result.model_version})
            return result
