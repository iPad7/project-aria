"""합성 루트 (composition root).

여기가 common과 contexts를 함께 아는 유일한 자리다 — 그래서 common 안이 아니라
패키지 최상위에 둔다(커널 순수성 계약 유지). 컨텍스트 라우터를 조립한다.
"""

from __future__ import annotations

from fastapi import FastAPI

from aria.common.exception_handler import register_exception_handlers
from aria.contexts.chat.adapter.inbound.http.router import router as chat_router
from aria.contexts.chat.adapter.inbound.ws.router import router as chat_ws_router
from aria.contexts.community.adapter.inbound.http.router import (
    like_router as community_like_router,
)
from aria.contexts.community.adapter.inbound.http.router import (
    router as community_router,
)
from aria.contexts.identity.adapter.inbound.http.router import router as identity_router
from aria.contexts.persona.adapter.inbound.http.router import router as persona_router


def create_app() -> FastAPI:
    app = FastAPI(title="aria", version="0.1.0")
    register_exception_handlers(app)
    app.include_router(identity_router)
    app.include_router(persona_router)
    app.include_router(chat_router)
    app.include_router(chat_ws_router)
    app.include_router(community_router)
    app.include_router(community_like_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
