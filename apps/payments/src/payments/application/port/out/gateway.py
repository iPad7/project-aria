"""결제 게이트웨이 포트 (Toss).

**앱은 어느 PG인지 모른다.** aria가 `PersonaLLMPort` 뒤에서 vLLM인지 OpenAI인지 모르는
것과 같은 경계다. 승인 결과만 알면 되고, 그 뒤가 Toss든 stub이든 유스케이스는 같다.

계약이 얇은 것은 의도다 — 지금 필요한 것은 "이 주문이 정말 결제됐는가"뿐이다.
빌링키·부분취소 같은 것은 그 기능이 실제로 필요해질 때 늘린다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Approval:
    """게이트웨이가 승인한 결제.

    `amount`를 함께 받는 이유: **우리가 기록한 금액과 대조하기 위해서**다. 클라이언트가
    보내온 값을 그대로 믿으면 1,000원짜리 주문으로 100,000 크레딧을 받아 갈 수 있다.
    """

    provider_key: str
    amount: int


class GatewayError(Exception):
    """게이트웨이가 승인을 거절했거나 응답하지 않았다."""


class PaymentGatewayPort(Protocol):
    async def confirm(self, order_id: str, provider_key: str, amount: int) -> Approval:
        """승인을 확정한다. 실패하면 `GatewayError`."""
        ...

    async def cancel(self, provider_key: str, reason: str) -> None:
        """승인을 취소(환불)한다. 실패하면 `GatewayError`."""
        ...
