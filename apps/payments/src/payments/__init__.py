"""payments — payment saga service (Toss + outbox)."""

__version__ = "0.1.0"


def main() -> None:
    """Run the payments service. `uv run payments`"""
    import uvicorn

    uvicorn.run("payments.app:app", host="0.0.0.0", port=8001, reload=True)
