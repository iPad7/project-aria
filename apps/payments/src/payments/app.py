"""payments 서비스 — FastAPI 합성 루트 (결제 준비·확정·환불).

**여기는 발행하지 않는다.** 확정이 남기는 것은 DB의 outbox 로우 하나이고, 브로커로
내보내는 것은 별도 진입점인 `payments.workers.outbox`다. 그 분리가 outbox 패턴이
사는 이유다 — 근거는 `docs/events.md`.

실행: `uv run payments` (relay는 `uv run python -m payments.workers.outbox`)
"""

from __future__ import annotations

from fastapi import FastAPI

from payments.adapter.inbound.http.router import router as payments_router
from payments.common.logging import configure_logging

configure_logging()

app = FastAPI(title="payments", version="0.1.0")
app.include_router(payments_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
