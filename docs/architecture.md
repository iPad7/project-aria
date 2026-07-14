# architecture

aria(모노레포: 앱 모놀리스 + payments 서비스)의 구조와 근거. 이 repo의 설계 원본.

## 배포 단위 (모노레포 + 최소 MSA)

| 단위 | 무엇 | 배포 | 왜 이 경계 |
|---|---|---|---|
| **aria 모놀리스** | contexts: identity·persona·community·chat·streaming·wallet | api / generation-worker / media-worker (같은 이미지) | 워커로 독립 스케일. wallet은 후원 핫패스라 여기 |
| **payments 서비스** | Toss 결제 saga · outbox | 별도(별도 DB) | 경계가 이미 async, 보안/장애 격리 |
| **inference 서빙** | vLLM 멀티-LoRA | 별도 repo(GPU) | GPU 하드 경계 |
| **llmops** | 데이터셋→SFT→DPO→평가→레지스트리 | 별도 repo(GPU) | 배치·GPU |

**추출 브라이트라인**: 다른 런타임(GPU) 또는 자연 async + 강한 격리 — 이 둘만 서비스. 나머지는 프로세스 타입 스케일.

## 레이어 (Hexagonal-Lite, 컨텍스트별 수직 슬라이스)

각 컨텍스트가 자기 `domain / application(+port) / adapter`를 가진다. `common`은 공유 커널(설정, `EventBusPort`, 컴포지션 루트 `app`).

`import-linter`(`apps/aria/.importlinter`)가 강제:
- 컨텍스트 내 `domain < application < adapter`
- 컨텍스트 간 **독립**(직접 import 금지, 경유는 `common` 이벤트/포트)
- `common`은 컨텍스트를 import하지 않음(커널 순수성)

`in`이 예약어라 필요 시 adapter 하위는 `inbound`/`outbound`.

## 커뮤니티(방송국) 컨텍스트

`community` = 스트리머별 팬덤 채널의 UGC(사연 게시판·좋아요·랭킹). `persona`(AI 캐릭터 설정)와 바뀌는 이유가 달라 별도 컨텍스트.
- **Story(사연)** 는 `community`가 소유(게시판 CRUD). `chat`의 idle은 직접 import 없이 **읽기 포트/이벤트**로 pending 사연을 소비(FR-STATION-4 → FR-IDLE-2).
- **랭킹(열혈순위)** 은 후원 이벤트 기반 read model.

## 앱 ↔ 추론 경계

`PersonaLLMPort`(`contexts/chat/application/port/out/llm.py`)가 유일한 접점. 앱은 `persona_id`만 넘기고 모델 버전을 모른다 — 버전 해석은 경계 너머 레지스트리 alias. 어댑터는 `contexts/chat/adapter/outbound/inference`. OpenAI fallback도 같은 포트 뒤.

추론 3역할(모두 OpenAI 호환 → 같은 어댑터, URL 스왑): 운영=vLLM(멀티-LoRA), fallback·로컬 dev=OpenAI, 학습·평가=transformers+trl+peft(별도 repo).

## 메시징 — 성격이 다른 둘

| | 채팅 팬아웃 | durable 이벤트 (LLM·미디어 seam · payments↔wallet) |
|---|---|---|
| 수단 | **Redis pub/sub** | **Kafka + FastStream** (`EventBusPort` 뒤) |
| 성격 | 저지연·고빈도·유실 허용 | durable·at-least-once·consumer group·outbox |

payments→wallet은 **outbox 패턴**(결제확정 + outbox 로우 로컬 트랜잭션 → relay가 Kafka 발행 → wallet 멱등 소비). 상세: `docs/events.md`.

## 실시간 송출 — 서버합성 HLS

미디어는 서버합성 HLS + CDN. media-worker가 TTS + 감정별 클립 + 자막을 ffmpeg로 muxing → HLS 세그먼트. 시청자 수가 CDN으로 이관(레거시 "100명 벽" 병목 제거). 응답 사이는 idle 아바타 루프. 채팅·제어는 WebSocket(+Redis pub/sub 백플레인).

## 관측성 — 두 레이어

- 시스템: OpenTelemetry SDK(FastAPI·httpx·redis·kafka·sqlalchemy) → SigNoz.
- LLM: 토큰·비용·지연·품질을 모델 버전별 → Langfuse(생성 어댑터에서 계측).

## 데이터

- PostgreSQL(SQLModel+Alembic): aria DB(페르소나·유저·방·메시지·사연·wallet) / **payments DB 별도**(결제기록·outbox).
- Redis: 세션·큐·토픽 상태(수평확장 위해 프로세스 메모리 금지) + pub/sub.
- 상세: `docs/data-model.md`.
