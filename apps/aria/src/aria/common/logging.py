"""로깅 설정 — 세 진입점이 같은 포맷으로 찍게 한다.

**시각이 없으면 로그는 사후에 못 읽는다.** `logging.basicConfig(level=...)`의 기본
포맷에는 타임스탬프가 없어서, 진행 워커가 남긴 수백 줄을 놓고 "이게 언제 일이냐"를
답할 수 없었다. 방이 방치된 채 돌던 것을 알아채는 데 오래 걸린 이유이기도 하다.

로거 이름을 함께 찍는다 — 한 프로세스 안에서 진행 루프와 라이브러리(kafka·httpx)의
줄이 섞이므로 어디서 나온 줄인지가 필요하다.
"""

from __future__ import annotations

import logging

from aria.common.config import settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"


def configure_logging() -> None:
    """루트 로거를 설정한다. 각 합성 루트가 맨 처음에 부른다.

    `basicConfig`는 루트에 핸들러가 이미 있으면 아무 것도 하지 않는다 — uvicorn은
    자기 로거(`uvicorn.*`)에만 핸들러를 달고 루트는 건드리지 않으므로 api에서도
    우리 로거는 이 포맷을 탄다. `force=True`로 덮어쓰지 않는 것은 의도적이다:
    호스트가 이미 로깅을 구성했다면 그쪽이 옳다.
    """
    logging.basicConfig(level=settings.log_level.upper(), format=_FORMAT)
