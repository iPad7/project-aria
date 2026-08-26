"""PersonaLLMPort 두 개를 합성하는 데코레이터 어댑터.

NFR-REL-3("sLLM 장애 시 OpenAI 폴백으로 응답 지속", docs/prd.md)을 실체화한다.
자체 vLLM 서빙이 붙으면 그게 단일 장애점이 되는데, GPU 박스가 죽었다고 방송이
멈춰서는 안 된다.

이 클래스는 PersonaLLMPort를 **구현하면서 동시에 PersonaLLMPort 둘을 받는다**.
그래서 application은 폴백의 존재를 모른다 — ChatOrchestrationService는 포트 하나만
본다. 누가 응답했는지는 LLMResult.model_version에 자연스럽게 드러나므로, 관측은
되지만 분기는 못 하는 포트 규칙이 그대로 유지된다.

`client.py`가 예고해 둔 두 갈래("compose it here or as a decorating adapter") 중
후자다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from aria.contexts.chat.application.port.out.llm import (
    GenParams,
    LLMResult,
    Message,
    PersonaLLMPort,
)

logger = logging.getLogger(__name__)


class FallbackPersonaLLM:
    def __init__(self, primary: PersonaLLMPort, fallback: PersonaLLMPort) -> None:
        self._primary = primary
        self._fallback = fallback

    async def generate(
        self,
        persona_id: str,
        messages: Sequence[Message],
        params: GenParams | None = None,
    ) -> LLMResult:
        try:
            return await self._primary.generate(persona_id, messages, params)
        except Exception:
            # 예외 종류를 좁히지 않는다. 이 래퍼의 계약은 "주가 실패하면 폴백"이고,
            # 무엇이 실패인지는 주 어댑터의 사정이다. 여기서 openai 예외 계층을 알면
            # 경계가 흐려지고, 주 백엔드 구현이 바뀌면 조용히 안 잡히게 된다.
            # 버그를 삼키는 위험은 exc_info 로그와 model_version 변화로 드러난다.
            logger.warning(
                "주 LLM 백엔드 실패 — 폴백으로 전환합니다 (persona_id=%s)",
                persona_id,
                exc_info=True,
            )
            # 폴백까지 실패하면 그 예외를 그대로 올린다 — 숨길 수단이 더는 없다.
            return await self._fallback.generate(persona_id, messages, params)
