"""`PersonaLLMPort` 구현 선택 — config를 어댑터로 바꾸는 곳.

C-4-1 전에는 이 조립이 `adapter/inbound/deps.py`(api의 DI)에 있었다. 생성이 워커로
빠지면서 **api는 LLM을 아예 모르게 됐으므로** 여기로 옮겼다. api가 쓰지도 않을
OpenAI 클라이언트를 import 시점에 만들 이유가 없다.

지금 이걸 쓰는 것은 generation-worker의 합성 루트 하나뿐이다.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from aria.common.config import settings
from aria.common.tracing import NoOpTracing, TracingPort
from aria.contexts.chat.adapter.outbound.inference.fallback import FallbackPersonaLLM
from aria.contexts.chat.adapter.outbound.inference.openai_compat import OpenAICompatLLM
from aria.contexts.chat.adapter.outbound.inference.stub import StubPersonaLLM
from aria.contexts.chat.adapter.outbound.inference.traced import TracedPersonaLLM
from aria.contexts.chat.application.port.out.llm import PersonaLLMPort


def _traced(inner: PersonaLLMPort, tracing: TracingPort, name: str) -> PersonaLLMPort:
    return TracedPersonaLLM(inner, tracing, name=name)


def _build_primary_llm() -> PersonaLLMPort:
    # config로 스텁/실제 생성 선택. 기본 stub → 키 없는 로컬·CI 그대로 통과.
    # vLLM은 OpenAI 호환이라 base_url만 바꾸면 같은 어댑터로 자체 서빙(A-2)에도 쓴다.
    if settings.llm_backend == "openai":
        client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.openai_api_key or "not-needed",
        )
        return OpenAICompatLLM(client, settings.llm_model)
    return StubPersonaLLM()


def _build_fallback_llm() -> PersonaLLMPort:
    # 폴백은 항상 진짜 OpenAI다(base_url을 주지 않음) — 주 백엔드가 자체 서빙일 때
    # 같은 인프라로 폴백하면 의미가 없기 때문. docs/architecture.md 참조.
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    return OpenAICompatLLM(client, settings.llm_fallback_model)


def build_llm(tracing: TracingPort | None = None) -> PersonaLLMPort:
    """계측을 **폴백 안쪽**에 감싼다.

    바깥에 감싸면 호출 1건에 generation 1개가 남아, 폴백이 일어난 사실이 트레이스에서
    사라지고 `model_version`으로만 추측하게 된다. 안쪽에 감싸면 폴백 시 generation이
    **두 개** 남아 "주 백엔드가 실패해서 폴백했다"가 그대로 보인다.
    """
    tracing = tracing or NoOpTracing()
    primary = _traced(_build_primary_llm(), tracing, "llm:primary")
    if not settings.llm_fallback_enabled:
        return primary
    if not settings.openai_api_key:
        # 신뢰성 요구사항(NFR-REL-3)을 켜뒀는데 조용히 폴백 없이 도는 게 최악이다.
        # 설정 오류는 기동 시점에 시끄럽게 죽는 편이 낫다.
        raise RuntimeError(
            "ARIA_LLM_FALLBACK_ENABLED=true 인데 ARIA_OPENAI_API_KEY 가 없습니다. "
            "키를 주거나 폴백을 끄세요."
        )
    return FallbackPersonaLLM(
        primary, _traced(_build_fallback_llm(), tracing, "llm:fallback")
    )
