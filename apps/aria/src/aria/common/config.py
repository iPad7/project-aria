"""Application settings (composition-root input)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ARIA_", extra="ignore")

    # Persistence / state
    database_url: str = "postgresql+psycopg://aria:aria@localhost:5432/aria"
    redis_url: str = "redis://localhost:6379/0"

    # app/inference boundary: persona_id -> inference serving base URL.
    # Empty -> OpenAI fallback behind PersonaLLMPort.
    inference_base_urls: dict[str, str] = {}
    openai_api_key: str | None = None


settings = Settings()
