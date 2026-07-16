# aria

AI 페르소나 **라이브 스트리밍 플랫폼** (모노레포). 페르소나가 각자 성격·말투로 시청자 채팅에 실시간 반응하고, 응답을 TTS 음성 + 아바타 영상 + 자막으로 합성해 라이브로 송출한다. 초기 버티컬: 연애상담.

> 설계 근거와 결정은 `docs/architecture.md`.

## 구성 (모노레포 · uv workspace)

```
apps/
  aria/          # 앱 모놀리스 (FastAPI, Hexagonal-Lite) — api/generation-worker/media-worker
    src/aria/
      contexts/{identity,persona,chat,streaming,wallet}/{domain,application,adapter}
      common/    # shared kernel: config, EventBusPort, app(composition root)
  payments/      # 별도 서비스 (Toss 결제 saga + outbox)
    src/payments/{domain,application,adapter}
docs/
```

> 별도 배포: **inference**(vLLM/GPU) · **llmops**는 GPU라 별도 repo.
> 경계(import-linter 강제): 컨텍스트 내 `domain < application < adapter`, 컨텍스트 간 독립(경유는 `common`).

## Quickstart

```bash
uv sync
uv run aria        # http://localhost:8000/health   (앱 모놀리스)
uv run payments    # http://localhost:8001/health   (결제 서비스)

uv run lint-imports --config apps/aria/.importlinter
uv run lint-imports --config apps/payments/.importlinter
uv run pytest
```

`uv run aria`만으로 `/health`는 뜨지만(연결은 지연), 회원가입·페르소나·채팅 등 상태를
쓰는 기능은 Postgres·Redis가 필요합니다.

## 로컬 인프라 (Postgres · Redis · 마이그레이션)

```bash
docker compose up -d postgres redis   # 로컬 인프라 (docker-compose.yml)

cd apps/aria
uv run alembic upgrade head           # 스키마 반영 (마이그레이션이 단일 소스)
uv run uvicorn aria.app:app --reload  # http://localhost:8000
```

- 접속 정보는 `config.Settings` 기본값이 compose와 일치 — 로컬은 `.env` 없이 동작(운영은 `ARIA_*`로 override, `.env.example` 참고)
- 스키마 변경 시 모델 수정 후 `uv run alembic revision --autogenerate -m "..."` → 생성된 마이그레이션 검토 → `upgrade head` (생성물은 ruff로 자동 정리됨)
- 테스트는 인메모리 SQLite·fakeredis라 위 인프라 없이 `uv run pytest`로 실행됨
