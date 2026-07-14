"""Inbound web adapter — FastAPI REST + WebSocket entrypoint.

Skeleton: only /health for now. Routers and the WebSocket streaming endpoint
get wired here via the composition root (aria.common.container). See docs/architecture.md.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="aria", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
