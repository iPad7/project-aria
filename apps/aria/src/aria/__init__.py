"""aria — AI persona live-streaming platform."""

__version__ = "0.1.0"


def main() -> None:
    """Run the web adapter. `uv run aria`"""
    import uvicorn

    uvicorn.run("aria.app:app", host="0.0.0.0", port=8000, reload=True)
