"""identity 도메인 모델.

도메인은 순수하다 — 해싱·토큰·DB를 모른다. `password_hash`는 이미 해싱된
불투명 문자열이며, 어떻게 만들어지는지는 application 포트(PasswordHasher)의 몫이다.
"""

from __future__ import annotations

from pydantic import EmailStr, Field

from aria.common.domain import Entity


class User(Entity):
    email: EmailStr
    username: str = Field(min_length=2, max_length=30)
    password_hash: str
    is_active: bool = True
