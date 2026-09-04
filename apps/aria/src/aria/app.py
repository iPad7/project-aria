"""합성 루트 (composition root).

여기가 common과 contexts를 함께 아는 유일한 자리다 — 그래서 common 안이 아니라
패키지 최상위에 둔다(커널 순수성 계약 유지). 컨텍스트 라우터를 조립하고, 컨텍스트 간
포트를 배선한다.
"""

from __future__ import annotations

from fastapi import FastAPI

from aria.common.exception_handler import register_exception_handlers
from aria.contexts.chat.adapter.inbound import deps as chat_deps
from aria.contexts.chat.adapter.inbound.http.router import router as chat_router
from aria.contexts.chat.adapter.inbound.ws.router import router as chat_ws_router
from aria.contexts.community.adapter.inbound.http import deps as community_deps
from aria.contexts.community.adapter.inbound.http.router import (
    like_router as community_like_router,
)
from aria.contexts.community.adapter.inbound.http.router import (
    ranking_router as community_ranking_router,
)
from aria.contexts.community.adapter.inbound.http.router import (
    router as community_router,
)
from aria.contexts.identity.adapter.inbound import deps as identity_deps
from aria.contexts.identity.adapter.inbound.http.router import router as identity_router
from aria.contexts.persona.adapter.inbound.http.router import router as persona_router
from aria.contexts.wallet.adapter.inbound import deps as wallet_deps
from aria.contexts.wallet.adapter.inbound.http.router import router as wallet_router


def create_app() -> FastAPI:
    app = FastAPI(title="aria", version="0.1.0")
    register_exception_handlers(app)
    app.include_router(identity_router)
    app.include_router(persona_router)
    app.include_router(chat_router)
    app.include_router(chat_ws_router)
    app.include_router(community_router)
    app.include_router(community_like_router)
    app.include_router(community_ranking_router)
    app.include_router(wallet_router)

    # 컨텍스트 간 포트 배선. 소비자는 `common`의 계약만 알고, 구현자는 소비자를 모른다
    # — 둘을 아는 곳은 여기뿐이다. 배선이 빠지면 조용히 잘못 도는 대신
    # NotImplementedError로 죽는다.
    #
    # chat ← wallet: 후원 결제(FR-PAY-3)
    app.dependency_overrides[chat_deps.get_superchat] = wallet_deps.get_superchat
    # community ← wallet · identity: 열혈순위(FR-STATION-6)는 금액과 이름을 각각
    # 다른 컨텍스트에서 받아 합친다.
    app.dependency_overrides[community_deps.get_donation_ranking] = (
        wallet_deps.get_donation_ranking
    )
    app.dependency_overrides[community_deps.get_user_directory] = (
        identity_deps.get_user_directory
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
