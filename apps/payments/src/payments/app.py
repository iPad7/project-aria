"""Payments service — FastAPI entrypoint (Toss webhooks, payment saga, outbox).

Skeleton: only /health for now. Webhook router + saga + outbox relay wired later.
See docs/architecture.md, docs/events.md.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="payments", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
