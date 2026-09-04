"""설정 검증.

실제로 띄워 보고 찾은 것: `.env.example` 을 그대로 복사하면 `ARIA_JWT_SECRET=` 이
빈 값으로 들어가 안전한 기본값을 **덮어쓰고**, 기동은 멀쩡히 된 뒤 **첫 로그인에서**
`HMAC key must not be empty` 로 죽었다.
"""

import pytest

from aria.common.config import Settings


def test_empty_jwt_secret_is_rejected_at_startup() -> None:
    # 빈 문자열도 pydantic-settings 에게는 "설정된 값"이라 기본값을 대체한다.
    # 설정 실수는 첫 로그인이 아니라 기동 시점에 드러나야 한다.
    with pytest.raises(ValueError, match="ARIA_JWT_SECRET"):
        Settings(jwt_secret="")


def test_whitespace_only_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValueError, match="ARIA_JWT_SECRET"):
        Settings(jwt_secret="   ")


def test_default_jwt_secret_is_usable() -> None:
    # 줄을 지우면(=기본값) 개발이 그대로 돌아가야 한다.
    assert Settings().jwt_secret.strip()
