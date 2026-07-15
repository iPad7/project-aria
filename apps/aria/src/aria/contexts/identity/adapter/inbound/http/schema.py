"""identity HTTP 요청/응답 DTO. 도메인 엔티티가 아니라 직렬화 모양만 담는다."""

from __future__ import annotations

from uuid import UUID

from pydantic import EmailStr, Field

from aria.common.schema import SchemaBase


class RegisterRequest(SchemaBase):
    email: EmailStr
    username: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(SchemaBase):
    email: EmailStr
    password: str


class UserResponse(SchemaBase):
    # password_hash를 의도적으로 제외 — 비밀번호는 응답에 절대 담기지 않는다.
    id: UUID
    email: EmailStr
    username: str
    is_active: bool


class TokenResponse(SchemaBase):
    access_token: str
    token_type: str = "bearer"
