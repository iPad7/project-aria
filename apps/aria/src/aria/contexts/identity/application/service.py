"""identity 유스케이스. 포트만 알고 구체 구현(DB·argon2·JWT)은 모른다."""

from __future__ import annotations

from uuid import UUID

from aria.common.errors import ConflictError, NotFoundError, UnauthorizedError
from aria.contexts.identity.application.port.out.password_hasher import PasswordHasher
from aria.contexts.identity.application.port.out.repository import UserRepository
from aria.contexts.identity.application.port.out.token_service import TokenService
from aria.contexts.identity.domain.model import User

# 사용자 열거(존재 여부를 응답 시간으로 추론)를 막기 위한 더미 해시.
# 로그인 실패 시에도 항상 verify를 돌려 타이밍을 일정하게 유지한다.
# 실제 argon2 인코딩 문자열이어야 verify가 예외 없이 False를 돌려준다.
_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "SKmYXngpkTPBjAhuK0Htdg$04rFsgjpCfP7QN9R7roEaYIZ7J0+05Kw9hmknRJbEqI"
)


class IdentityService:
    def __init__(
        self,
        users: UserRepository,
        hasher: PasswordHasher,
        tokens: TokenService,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._tokens = tokens

    def register(self, email: str, username: str, raw_password: str) -> User:
        if self._users.get_by_email(email) is not None:
            raise ConflictError("이미 가입된 이메일입니다", code="email_taken")
        user = User(
            email=email,
            username=username,
            password_hash=self._hasher.hash(raw_password),
        )
        self._users.add(user)
        return user

    def authenticate(self, email: str, raw_password: str) -> str:
        user = self._users.get_by_email(email)
        # 사용자가 없어도 verify를 돌려 타이밍 사이드채널을 제거한다.
        hashed = user.password_hash if user is not None else _DUMMY_HASH
        password_ok = self._hasher.verify(raw_password, hashed)
        if user is None or not password_ok:
            raise UnauthorizedError(
                "이메일 또는 비밀번호가 올바르지 않습니다", code="invalid_credentials"
            )
        if not user.is_active:
            raise UnauthorizedError("비활성화된 계정입니다", code="inactive_account")
        return self._tokens.issue_access_token(user.id, is_staff=user.is_staff)

    def get_user(self, user_id: UUID) -> User:
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("사용자를 찾을 수 없습니다", code="user_not_found")
        return user
