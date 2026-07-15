"""API DTO base (요청/응답).

도메인 엔티티(domain.Entity)가 아니라 직렬화 관심사만 담는다.
`from_attributes=True`로 ORM 객체·도메인 엔티티를 응답 DTO로 변환할 수 있다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SchemaBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
