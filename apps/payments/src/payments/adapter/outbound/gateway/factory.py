"""설정을 보고 게이트웨이 구현을 고른다.

지금은 갈래가 하나뿐이라 과해 보이지만, 여기가 **Toss 어댑터가 들어올 유일한 자리**다.
합성 루트마다 `if settings.payment_gateway == ...`를 흩어 두지 않기 위해 미리 만든다 —
api와 (앞으로의) 배치가 같은 선택을 하게 하는 것이 목적이다.
"""

from __future__ import annotations

from payments.adapter.outbound.gateway.stub import StubPaymentGateway
from payments.application.port.out.gateway import PaymentGatewayPort
from payments.common.config import settings


def build_gateway() -> PaymentGatewayPort:
    if settings.payment_gateway == "stub":
        return StubPaymentGateway()
    raise ValueError(
        f"알 수 없는 결제 게이트웨이: {settings.payment_gateway} (지원: stub)"
    )
