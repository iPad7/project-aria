"""PersonaLLMPort의 스텁 구현.

실제 vLLM/추론 서버 연동은 이번 범위 밖(streaming 슬라이스와 함께). 그때까지
로컬 실행·데모가 동작하도록 캔드 응답을 돌려준다. app은 이게 스텁인지 sLLM인지
모른다 — 그게 포트의 요점이다.
"""

from __future__ import annotations

from typing import Sequence

from aria.contexts.chat.application.port.out.llm import (
    GenParams,
    LLMResult,
    Message,
)


class StubPersonaLLM:
    async def generate(
        self,
        persona_id: str,
        messages: Sequence[Message],
        params: GenParams | None = None,
    ) -> LLMResult:
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )
        return LLMResult(
            text=f"[{persona_id}] 잘 들었어요: {last_user}",
            model_version="stub",
        )
