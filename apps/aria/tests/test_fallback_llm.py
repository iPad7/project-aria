"""FallbackPersonaLLM 단위 테스트 + 합성 배선 검증.

가짜 PersonaLLMPort 구현을 주입해 네트워크 없이 검증한다 — 주가 성공하면 폴백을
건드리지 않고, 주가 실패하면 폴백 결과를 돌려주며, 둘 다 실패하면 폴백의 예외가
올라오는지. 아래쪽은 build_llm()이 config에 따라 실제로 합성하는지(그리고 키 없이
폴백을 켜면 기동을 거부하는지) 본다.
"""

import logging
from contextlib import contextmanager

import pytest

from aria.common.config import settings
from aria.common.tracing import _NullObservation
from aria.contexts.chat.adapter.outbound.inference.factory import build_llm
from aria.contexts.chat.adapter.outbound.inference.fallback import FallbackPersonaLLM
from aria.contexts.chat.adapter.outbound.inference.stub import StubPersonaLLM
from aria.contexts.chat.adapter.outbound.inference.traced import TracedPersonaLLM
from aria.contexts.chat.application.port.out.llm import (
    GenParams,
    LLMResult,
    Message,
)


class _FakeLLM:
    """호출을 기록하는 가짜 포트 구현. error를 주면 그 예외를 던진다."""

    def __init__(self, *, model_version: str, error: Exception | None = None) -> None:
        self._model_version = model_version
        self._error = error
        self.calls: list[tuple[str, tuple[Message, ...], GenParams | None]] = []

    async def generate(self, persona_id, messages, params=None) -> LLMResult:
        self.calls.append((persona_id, tuple(messages), params))
        if self._error is not None:
            raise self._error
        return LLMResult(
            text=f"{self._model_version} 응답", model_version=self._model_version
        )


_MESSAGES = [Message(role="user", content="사연이 있어요")]


class _RecordingTracing:
    """`TracingPort` 스텁 — 어떤 구간이 어떤 순서로 열렸는지만 본다."""

    def __init__(self) -> None:
        self.names: list[str] = []

    @contextmanager
    def observe(self, name, *, kind="span", input=None, metadata=None, model=None):
        self.names.append(name)
        yield _NullObservation()

    def flush(self) -> None: ...


async def test_primary_success_does_not_touch_fallback() -> None:
    primary = _FakeLLM(model_version="vllm")
    fallback = _FakeLLM(model_version="openai")
    llm = FallbackPersonaLLM(primary, fallback)

    result = await llm.generate("persona-1", _MESSAGES)

    assert result.model_version == "vllm"
    assert len(primary.calls) == 1
    assert fallback.calls == []  # 폴백은 건드리지 않는다


async def test_primary_failure_falls_back() -> None:
    primary = _FakeLLM(model_version="vllm", error=ConnectionError("GPU 박스 다운"))
    fallback = _FakeLLM(model_version="openai")
    llm = FallbackPersonaLLM(primary, fallback)

    result = await llm.generate("persona-1", _MESSAGES)

    assert result.model_version == "openai"  # 서빙 주체가 model_version에 드러난다
    assert result.text == "openai 응답"
    assert len(fallback.calls) == 1


async def test_fallback_receives_same_arguments() -> None:
    primary = _FakeLLM(model_version="vllm", error=RuntimeError("boom"))
    fallback = _FakeLLM(model_version="openai")
    llm = FallbackPersonaLLM(primary, fallback)

    params = GenParams(max_tokens=64, temperature=0.7)
    await llm.generate("persona-9", _MESSAGES, params)

    assert fallback.calls[0] == ("persona-9", tuple(_MESSAGES), params)


async def test_both_failing_propagates_fallback_error() -> None:
    primary = _FakeLLM(model_version="vllm", error=RuntimeError("주 실패"))
    fallback = _FakeLLM(model_version="openai", error=RuntimeError("폴백도 실패"))
    llm = FallbackPersonaLLM(primary, fallback)

    # 더 숨길 수단이 없으므로 폴백의 예외가 그대로 올라온다.
    with pytest.raises(RuntimeError, match="폴백도 실패"):
        await llm.generate("persona-1", _MESSAGES)


async def test_arbitrary_exception_triggers_fallback_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 예외 종류를 좁히지 않는다는 결정의 이면 — 어떤 예외든 폴백이 뜨지만,
    # 삼켜지지 않도록 반드시 로그에 스택이 남아야 한다.
    primary = _FakeLLM(model_version="vllm", error=ValueError("예상 못 한 버그"))
    fallback = _FakeLLM(model_version="openai")
    llm = FallbackPersonaLLM(primary, fallback)

    with caplog.at_level(logging.WARNING):
        result = await llm.generate("persona-1", _MESSAGES)

    assert result.model_version == "openai"
    assert "예상 못 한 버그" in caplog.text  # 스택이 남는다(exc_info=True)


# --- 합성 배선 (build_llm) ---------------------------------------------------


def test_build_llm_without_fallback_has_no_fallback_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 기본값. 폴백을 끄면 폴백 데코레이터를 씌우지 않는다 — 키 없는 로컬·CI 경로.
    # llm_backend도 고정한다 — 주변 ARIA_* 환경변수에 결과가 흔들리지 않도록.
    #
    # 계측 데코레이터는 **항상** 붙는다(관측이 꺼져 있어도 no-op로). 경로를 하나로
    # 두어야 운영과 테스트의 객체 그래프가 갈라지지 않는다.
    monkeypatch.setattr(settings, "llm_backend", "stub")
    monkeypatch.setattr(settings, "llm_fallback_enabled", False)

    llm = build_llm()

    assert isinstance(llm, TracedPersonaLLM)
    assert not isinstance(llm, FallbackPersonaLLM)
    assert isinstance(llm._inner, StubPersonaLLM)


async def test_tracing_wraps_each_backend_inside_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """폴백이 일어나면 generation이 **두 개** 남아야 한다.

    바깥에 감싸면 호출 1건에 1개만 남아 "주 백엔드가 실패해서 폴백했다"가 트레이스에서
    사라진다. 안쪽에 감싸면 그 사실이 그대로 보인다.
    """
    monkeypatch.setattr(settings, "llm_backend", "stub")
    monkeypatch.setattr(settings, "llm_fallback_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    tracing = _RecordingTracing()

    llm = build_llm(tracing)
    # 주(stub)를 실패시켜 폴백을 태운다.
    llm._primary._inner = _FakeLLM(model_version="vllm", error=RuntimeError("죽음"))
    llm._fallback._inner = _FakeLLM(model_version="openai")

    await llm.generate("p", _MESSAGES)

    assert tracing.names == ["llm:primary", "llm:fallback"]


def test_build_llm_with_fallback_composes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_backend", "stub")
    monkeypatch.setattr(settings, "llm_fallback_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")

    assert isinstance(build_llm(), FallbackPersonaLLM)


def test_build_llm_refuses_fallback_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # NFR-REL-3을 켜뒀는데 조용히 폴백 없이 도는 것이 최악이다 — 기동을 거부한다.
    monkeypatch.setattr(settings, "llm_fallback_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", None)

    with pytest.raises(RuntimeError, match="ARIA_OPENAI_API_KEY"):
        build_llm()
