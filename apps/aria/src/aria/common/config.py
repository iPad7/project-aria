"""Application settings (composition-root input)."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="ARIA_", extra="ignore"
    )

    # Persistence / state
    database_url: str = "postgresql+psycopg://aria:aria@localhost:5432/aria"
    redis_url: str = "redis://localhost:6379/0"

    # Durable event backbone (EventBusPort -> Kafka). The generation worker consumes
    # from here; the API only publishes. Creating the broker does not connect, so
    # keyless local runs and CI stay green without a broker.
    kafka_bootstrap_servers: str = "localhost:9092"
    # Consumer group for generation workers. Scaling out = more replicas in this group.
    generation_consumer_group: str = "generation-workers"
    # How long a processed-message claim lives. Must outlast the redelivery window,
    # or a redelivery arriving after expiry slips through as a duplicate reply.
    dedup_ttl_seconds: int = 3600

    # app/inference boundary: persona_id -> inference serving base URL.
    # Empty -> OpenAI fallback behind PersonaLLMPort.
    inference_base_urls: dict[str, str] = {}
    openai_api_key: str | None = None

    # Real generation behind PersonaLLMPort (A-1). vLLM serves an OpenAI-compatible
    # API, so one adapter covers external OpenAI and self-hosted vLLM — only the
    # base URL differs. Default "stub" keeps keyless local runs and CI green.
    llm_backend: Literal["stub", "openai"] = "stub"
    llm_base_url: str | None = None  # None -> api.openai.com; set -> vLLM/custom
    llm_model: str = "gpt-4o-mini"  # A-2 target: "skt/A.X-4.0-Light"

    # NFR-REL-3: keep answering when the self-hosted sLLM is down. The fallback is
    # always real OpenAI (api.openai.com) -- hence no separate base URL. Default off
    # so keyless local runs and CI stay green.
    llm_fallback_enabled: bool = False
    llm_fallback_model: str = "gpt-4o-mini"

    # Auth. Dev default is intentionally insecure — override via ARIA_JWT_SECRET.
    jwt_secret: str = "dev-insecure-do-not-use-in-production-secret"
    jwt_algorithm: str = "HS256"
    jwt_ttl_seconds: int = 3600


settings = Settings()
