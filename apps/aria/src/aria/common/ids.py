"""ID generation.

PK는 UUIDv7 — 시간순 정렬이 되어 B-tree 인덱스에 친화적이다(random UUIDv4의
인덱스 단편화 회피). 표준 stdlib에는 아직 uuid7이 없어 uuid-utils(Rust 구현)로
생성하되, DB·Pydantic 호환을 위해 표준 uuid.UUID로 되돌려 반환한다.
자세한 근거는 docs/data-model.md.
"""

from __future__ import annotations

import uuid

import uuid_utils


def new_id() -> uuid.UUID:
    """새 UUIDv7을 표준 uuid.UUID로 반환."""
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)
