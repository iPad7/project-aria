# aria — 작업 지침

AI 페르소나 라이브 스트리밍 플랫폼 (모노레포: `apps/aria` 모놀리스 + `apps/payments` 서비스). 설계 근거는 `docs/architecture.md`, 이벤트는 `docs/events.md`, 데이터는 `docs/data-model.md`.

## 핵심 규칙

- **패키지 관리는 uv (workspace).** `uv add --package <아리아|payments> …` / `uv sync` / `uv run`. pip 금지.
- **경계** (`import-linter`로 강제, 패키지별 `.importlinter`):
  - 컨텍스트 내 `domain < application < adapter`
  - 컨텍스트 간 **독립** — 직접 import 금지, 경유는 `common`(이벤트/포트)
  - `common`(공유 커널)은 컨텍스트를 import하지 않음
  - `uv run lint-imports --config apps/aria/.importlinter` (payments 동일)
- **앱/추론 경계**: 앱은 `persona_id`만 넘기고 모델 버전/추론 세부를 **절대 모른다**. `PersonaLLMPort` 계약으로만. OpenAI fallback도 그 포트 뒤.
- **착수 순서**: 앱(레거시 기능 재현) 먼저. ML/LLMOps는 포트 stub으로 두고 이후.
- 설계·구현 문서는 `docs/`에 두고 **자기완결**로 쓴다 (repo 밖 사설 문서를 역참조하지 않음).

## 확정 스택

FastAPI · SQLModel+Alembic · Redis(state+pub/sub) · **Kafka+FastStream**(durable 이벤트) · ffmpeg→HLS · JWT · **vLLM**(서빙, 앱 밖) · OTel+SigNoz+Langfuse · Vite+React(별도 repo). payments=별도 서비스(outbox). Ollama·Redis Streams·Celery·Kafka대체 미사용. inference·llmops는 GPU라 별도 repo. 상세 `docs/architecture.md`.
