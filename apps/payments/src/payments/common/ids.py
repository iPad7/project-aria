"""ID 생성 — UUIDv7.

시간순 정렬이라 B-tree 인덱스에 친화적이다. outbox에서 특히 값이 있다: 미발행 로우를
`id` 순으로 읽으면 그것이 곧 **기록된 순서**라 별도 정렬 컬럼이 필요 없다.
"""

from __future__ import annotations

import uuid

import uuid_utils


def new_id() -> uuid.UUID:
    """새 UUIDv7을 표준 uuid.UUID로 반환."""
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)
