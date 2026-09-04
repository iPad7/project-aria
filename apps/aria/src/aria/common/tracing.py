"""TracingPort — LLM 관측(NFR-OBS-2)의 계약.

**왜 라이브러리를 직접 쓰지 않나.** 그러면 application이 Langfuse를 알게 된다.
`llm_backend="stub"`·`TtsPort`와 같은 이유로 계약을 두고 **기본을 no-op**으로 둔다 —
키 없는 로컬·CI가 그대로 통과하고, 나중에 OTel 어댑터를 하나 더 놓을 자리도 생긴다.

**관측이 생성을 죽이면 안 된다.** 백엔드가 느리거나 죽어도 방송은 계속돼야 하므로,
어댑터는 어떤 예외도 밖으로 내보내지 않는다. 캐시 어댑터들과 같은 방침이다.

**계측 위치가 둘인 이유**는 어댑터 혼자서는 필요한 걸 다 못 보기 때문이다.
`PersonaLLMPort.generate()`는 `persona_id`와 `messages`만 받는다 — `room_id`·`source`·
`msg_id`·프로필 유무를 모른다. 그래서 바깥 span은 서비스가(맥락), 안쪽 generation은
어댑터가(호출) 만든다. `FallbackPersonaLLM`이 폴백하면 **안쪽이 두 개**가 되어
그 사실이 그대로 드러난다.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal, Protocol

# span: 일반 작업 구간 · generation: LLM 호출(토큰·비용이 붙는다)
ObservationKind = Literal["span", "generation"]


class Observation(Protocol):
    """시작된 관측 구간. 끝나기 전에 결과를 채워 넣는다."""

    def set_output(self, output: Any) -> None: ...

    def set_metadata(self, metadata: Mapping[str, Any]) -> None: ...


class TracingPort(Protocol):
    def observe(
        self,
        name: str,
        *,
        kind: ObservationKind = "span",
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
        model: str | None = None,
    ) -> Any:
        """관측 구간을 연다. 컨텍스트 매니저로 쓴다.

        중첩하면 부모-자식이 된다 — 바깥 span 안에서 연 generation은 그 span에 달린다.
        """
        ...

    def flush(self) -> None:
        """버퍼를 비운다. 짧게 살다 죽는 프로세스가 끝나기 전에 부른다."""
        ...


class _NullObservation:
    def set_output(self, output: Any) -> None: ...

    def set_metadata(self, metadata: Mapping[str, Any]) -> None: ...


class NoOpTracing:
    """기본 구현. 아무 데도 보내지 않는다.

    관측을 끄는 것이 **정상 상태**다 — 키 없는 로컬과 CI가 여기로 돈다. 계측 코드가
    `if tracing_enabled:` 로 도배되지 않도록, 끄는 방법을 분기가 아니라 구현 교체로 둔다.
    """

    @contextmanager
    def observe(
        self,
        name: str,
        *,
        kind: ObservationKind = "span",
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
        model: str | None = None,
    ) -> Iterator[Observation]:
        yield _NullObservation()

    def flush(self) -> None: ...
