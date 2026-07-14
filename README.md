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
