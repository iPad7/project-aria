"""identity HTTP 라우터. 얇게 — 검증·조립은 DTO/의존성이, 규칙은 서비스가 맡는다."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from aria.contexts.identity.adapter.inbound.http.deps import (
    get_current_user,
    get_identity_service,
)
from aria.contexts.identity.adapter.inbound.http.schema import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from aria.contexts.identity.application.service import IdentityService
from aria.contexts.identity.domain.model import User

router = APIRouter(prefix="/auth", tags=["identity"])


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
def register(
    body: RegisterRequest,
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> User:
    return service.register(body.email, body.username, body.password)


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> TokenResponse:
    return TokenResponse(access_token=service.authenticate(body.email, body.password))


@router.get("/me", response_model=UserResponse)
def me(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    return current_user
