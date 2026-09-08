"""결제 게이트웨이 stub — 항상 승인한다.

**왜 stub이 기본값인가.** Toss 실연동은 시크릿 키와 공개된 webhook 수신 주소가 있어야
하고, 그건 이 단위의 관심사가 아니다(이벤트 배관). aria가 `llm_backend="stub"`으로
키 없이 도는 것과 같은 방침이라, 로컬과 CI가 결제 계정 없이 그대로 통과한다.

승인 금액을 **요청받은 금액 그대로** 돌려준다. 서비스의 금액 대조는 여기서는 늘
통과하지만, 그 검사는 진짜 게이트웨이를 위해 있는 것이라 stub 때문에 빼지 않는다.
"""

from __future__ import annotations

import logging

from payments.application.port.out.gateway import Approval

logger = logging.getLogger(__name__)


class StubPaymentGateway:
    async def confirm(self, order_id: str, provider_key: str, amount: int) -> Approval:
        logger.info(
            "stub 게이트웨이 승인 order_id=%s amount=%d (실제 결제 아님)",
            order_id,
            amount,
        )
        return Approval(provider_key=provider_key, amount=amount)

    async def cancel(self, provider_key: str, reason: str) -> None:
        logger.info(
            "stub 게이트웨이 취소 provider_key=%s reason=%s (실제 환불 아님)",
            provider_key,
            reason,
        )
