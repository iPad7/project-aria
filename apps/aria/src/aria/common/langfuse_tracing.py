"""TracingPort의 Langfuse 구현 (횡단 인프라, kafka.py의 형제).

`common`에 두는 이유는 `common/kafka.py`와 같다 — 계약이 커널에 있고 어느 컨텍스트도
import하지 않으므로 커널 순수성이 유지된다.

**어떤 예외도 밖으로 내보내지 않는다.** 관측 백엔드가 느리거나 죽었다고 방송이
멈추면 관측이 장애 원인이 된다. 실패하면 no-op 구간을 내주고 로그만 남긴다.

**샘플링을 켜 둔다.** Langfuse의 과금 단위는 trace가 아니라 **observation 하나하나**다
(span·generation·score 각각). 이 설계는 응답 1건이 3유닛쯤이고, idle 루프가 10초에
한 번 발화하므로 켜 두면 무료 한도가 빠르게 소진된다. `sample_rate`는 Langfuse
클라이언트가 trace 단위로 적용하므로, 샘플링돼도 **한 응답의 span과 generation이
같이 남거나 같이 빠진다** — 반쪽짜리 트레이스가 생기지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from langfuse import Langfuse

from aria.common.config import settings
from aria.common.tracing import Observation, ObservationKind, _NullObservation

logger = logging.getLogger(__name__)


class _LangfuseObservation:
    def __init__(self, span: Any) -> None:
        self._span = span

    def set_output(self, output: Any) -> None:
        try:
            self._span.update(output=output)
        except Exception:  # noqa: BLE001 - 관측이 생성을 죽이면 안 된다
            logger.debug("관측 출력 기록 실패 — 무시", exc_info=True)

    def set_metadata(self, metadata: Mapping[str, Any]) -> None:
        try:
            self._span.update(metadata=dict(metadata))
        except Exception:  # noqa: BLE001
            logger.debug("관측 메타데이터 기록 실패 — 무시", exc_info=True)


class LangfuseTracing:
    def __init__(self, client: Langfuse) -> None:
        self._client = client

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
        try:
            cm = self._client.start_as_current_observation(
                name=name,
                as_type=kind,
                input=input,
                metadata=dict(metadata) if metadata else None,
                model=model,
            )
        except Exception:  # noqa: BLE001 - 구간을 열지 못해도 생성은 계속된다
            logger.warning("관측 구간 시작 실패 — 계측 없이 진행", exc_info=True)
            yield _NullObservation()
            return

        with cm as span:
            yield _LangfuseObservation(span)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:  # noqa: BLE001
            logger.warning("관측 flush 실패 — 무시", exc_info=True)


def build_tracing() -> Any:
    """config에 따라 Langfuse 또는 no-op을 만든다.

    키가 없으면 **조용히 no-op**이다. 여기서 죽이지 않는 이유: 폴백 LLM(NFR-REL-3)과
    달리 관측은 없어도 서비스가 성립한다. 반대로 켜 뒀는데 키가 없으면 그건 설정
    실수이므로 경고를 남긴다.
    """
    from aria.common.tracing import NoOpTracing

    if not settings.langfuse_enabled:
        return NoOpTracing()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.warning(
            "ARIA_LANGFUSE_ENABLED=true 인데 키가 없습니다 — 관측 없이 진행합니다"
        )
        return NoOpTracing()

    return LangfuseTracing(
        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            sample_rate=settings.langfuse_sample_rate,
            environment=settings.langfuse_environment,
        )
    )
