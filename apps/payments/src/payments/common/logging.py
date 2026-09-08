"""로깅 설정 — api와 relay 워커가 같은 포맷으로 찍게 한다."""

from __future__ import annotations

import logging

from payments.common.config import settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"


def configure_logging() -> None:
    """루트 로거를 설정한다. 각 합성 루트가 맨 처음에 부른다."""
    logging.basicConfig(level=settings.log_level.upper(), format=_FORMAT)
