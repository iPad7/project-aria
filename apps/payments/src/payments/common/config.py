"""payments 설정 (합성 루트 입력).

`ARIA_*`가 아니라 **`PAYMENTS_*`**다. 접두사를 나눠야 한 머신에 둘을 함께 띄웠을 때
`DATABASE_URL` 하나가 두 서비스를 같은 DB로 보내는 사고가 나지 않는다 — DB가 갈려
있다는 것이 이 서비스를 분리한 이유의 절반이다.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="PAYMENTS_", extra="ignore"
    )

    # **aria와 다른 DB다.** 로컬은 같은 Postgres 서버의 다른 데이터베이스, 운영은
    # 별도 인스턴스(`docs/infra.md`). 같은 DB를 가리키게 되면 outbox의 전제 —
    # "상태 변경과 outbox 로우가 한 로컬 트랜잭션" — 은 그대로지만, 서비스를 나눈
    # 의미가 사라진다.
    database_url: str = "postgresql+psycopg://aria:aria@localhost:5432/payments"

    # relay가 발행할 백본. aria와 **같은** 클러스터다 — 그게 두 서비스의 접점이다.
    kafka_bootstrap_servers: str = "localhost:9092"

    # outbox relay 폴링. 결제 확정 후 크레딧이 보이기까지의 지연이 대략 이 값이다.
    # 1초면 사람이 "결제했는데 왜 안 들어와요"라고 하기 전에 끝난다.
    outbox_poll_seconds: float = 1.0
    # 한 번에 훑는 미발행 로우 수. 밀린 상황에서 한 바퀴가 너무 길어지지 않게 상한을
    # 둔다 — 남은 것은 다음 폴링이 가져간다.
    outbox_batch_size: int = 100

    # 결제 게이트웨이. 기본은 stub이라 키 없는 로컬·CI가 그대로 돈다(aria의
    # `llm_backend="stub"`과 같은 방침). Toss 실연동은 별도 단위다.
    payment_gateway: str = "stub"
    toss_secret_key: str | None = None

    log_level: str = "INFO"


settings = Settings()
