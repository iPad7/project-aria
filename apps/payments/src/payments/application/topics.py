"""payments가 발행하는 토픽과 페이로드 버전.

계약은 `docs/events.md`의 "payments ↔ wallet" 표다. 소비자는 aria의 wallet-workers이고,
**두 repo를 잇는 것은 이 문자열뿐**이다 — 컨텍스트 간 경유를 common으로 하듯, 서비스
간 경유는 토픽으로 한다.
"""

from __future__ import annotations

CREDIT_PURCHASE_CONFIRMED = "payments.credit-purchase-confirmed"
CREDIT_REFUNDED = "payments.credit-refunded"

# 페이로드 스키마 버전(C-4-2). 모르는 버전을 만난 소비자는 추측하지 않고 DLQ로 보낸다.
PAYLOAD_VERSION = 1


def purchase_idempotency_key(payment_id: str) -> str:
    """지급이 한 번만 적용되게 하는 열쇠.

    **결정적으로 만든다** — relay는 at-least-once라 같은 메시지가 두 번 올 수 있고,
    그때 두 메시지가 같은 키를 실어야 원장의 유일 제약이 두 번째를 막는다. 발행할
    때마다 새로 만들면(uuid) 중복 전달이 곧 이중 지급이 된다.
    """
    return f"payment:{payment_id}:purchase"


def refund_idempotency_key(payment_id: str) -> str:
    return f"payment:{payment_id}:refund"
