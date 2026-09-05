"""Application settings (composition-root input)."""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator
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

    # LLM observability (NFR-OBS-2 -> Langfuse). Off by default: keyless local runs
    # and CI go through the no-op tracer, so instrumentation needs no `if enabled`.
    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    # Langfuse bills per *observation* (span/generation/score), not per trace. One
    # response costs ~3, and the idle loop speaks every ~10s -- so sample by default.
    # Sampling is applied per trace, so a response's span and generation share a fate.
    langfuse_sample_rate: float = 0.2
    langfuse_environment: str = "development"

    # Idle progress (FR-IDLE). Legacy used a 6s silence threshold.
    idle_threshold_seconds: float = 6.0
    # Must be shorter than the threshold, or the loop keeps missing idle windows.
    idle_tick_seconds: float = 3.0
    # Rooms examined per tick. Parallelism and sharding stay out until load is
    # measured -- the same call as the deferred WS subscription sharing.
    idle_rooms_per_tick: int = 50
    # A live room nobody watches and nothing happens in gets closed after this long.
    # Long enough not to cut a demo short, short enough not to run all night.
    room_abandon_seconds: float = 1800.0

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

    @field_validator("jwt_secret")
    @classmethod
    def _secret_must_not_be_empty(cls, value: str) -> str:
        """빈 문자열은 기본값을 덮어쓰고 **첫 로그인 때** 터진다.

        `.env` 에 `ARIA_JWT_SECRET=` 를 빈 채로 두면 pydantic-settings 는 그것을
        "설정된 값"으로 보아 안전한 기본값을 대체한다. 그러면 기동은 멀쩡히 되고
        토큰 발급에서 `HMAC key must not be empty` 로 죽는다 — 설정 실수는
        기동 시점에 드러나는 편이 낫다(폴백 LLM 이 키 없이 켜졌을 때와 같은 방침).
        """
        if not value.strip():
            raise ValueError(
                "ARIA_JWT_SECRET 가 비어 있습니다. 값을 주거나 줄을 지우세요"
                "(지우면 개발용 기본값이 쓰입니다)."
            )
        return value


settings = Settings()
