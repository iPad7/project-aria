# 이벤트 명세

느리고 비싼 단계(LLM 생성·미디어 합성)와 크로스서비스(payments↔wallet)를 잇는 비동기 메시징. 전체 구조는 `docs/architecture.md`.

## 두 종류 — 혼동 금지

| | 채팅 팬아웃·알림 | durable seam |
|---|---|---|
| 수단 | **Redis pub/sub** | **Kafka + FastStream** (`EventBusPort`) |
| 성격 | 저지연·고빈도·**유실 허용** | durable·at-least-once·consumer group·재처리 |
| 예 | 채팅 브로드캐스트, 미디어 타임라인 알림 | 응답 생성·미디어 합성·크레딧 지급 |

---

## Kafka 토픽 (durable)

키는 파티션·순서 보장용. 소비는 consumer group으로 수평 확장.

### 코어 루프

| 토픽 | producer | consumer group | key | 페이로드 |
|---|---|---|---|---|
| `aria.chat.response-requested` | chat (댓글 선별 후) | generation-workers | room_id | msg_id, room_id, persona_id, selected_comment, context, requested_at |
| `aria.chat.superchat-requested` | chat (후원 수신) | generation-workers | room_id | msg_id, room_id, persona_id, donor, amount, message, requested_at |
| `aria.streaming.response-generated` | generation-worker | media-workers | room_id | msg_id, room_id, persona_id, text, emotion, model_version, generated_at |

> 미디어 합성 완료 후 시청자 알림(타임라인 splice)은 **Redis pub/sub**(아래). Kafka 토픽 아님.
> **superchat 우선 = 별도 토픽.** Kafka는 파티션 내 우선순위가 없어 필드로는 불가 → generation-worker가 `superchat` 토픽을 먼저 drain하고 없을 때만 normal을 처리(Kafka 우선순위 표준 패턴).

### payments ↔ wallet

| 토픽 | producer | consumer group | key | 페이로드 |
|---|---|---|---|---|
| `payments.credit-purchase-confirmed` | payments (outbox relay) | wallet-workers | user_id | payment_id, user_id, credits, idempotency_key, confirmed_at |
| `payments.credit-refunded` | payments (outbox relay) | wallet-workers | user_id | payment_id, user_id, credits, refunded_at |

---

## Redis pub/sub 채널 (휘발)

| 채널 | publisher | 용도 |
|---|---|---|
| `chat:room:{room_id}` | chat api/worker | 채팅·시스템 메시지 전 인스턴스 팬아웃 |
| `stream:room:{room_id}` | media-worker | 미디어 세그먼트 알림(타임라인 splice), 후원 표시, 큐 상태 |

---

## 전달 의미론

- **at-least-once**: consumer group이 처리 후 offset commit. 중복은 멱등키로 흡수 — `msg_id`(생성) / `idempotency_key`(크레딧).
- **순서**: 같은 `key`(room_id·user_id)는 같은 파티션 → 순서 보장.
- **outbox** (payments): `payment` 상태변경 + `outbox` 로우를 **한 로컬 트랜잭션**에 기록 → relay가 `outbox`를 폴링/CDC로 Kafka 발행 → `published`로 마킹. 이중발행·유실 방지.
- **DLQ**: N회 실패 시 `<topic>.dlq`로 이동, 수동/배치 재처리. (N·재시도 간격 미확정)
- **스키마 버저닝**: 페이로드에 `v` 필드 또는 스키마 레지스트리 — 미확정.

---

## 사연(Story) 소비 — **읽기 포트로 확정** (이벤트 아님)

idle이 사연을 읽는 흐름은 Kafka가 아니라 **동기 읽기 포트**로 한다.

- **`StoryFeedPort`는 `common/story_feed.py`에 둔다**: `claim_next_pending(persona_id) -> PendingStory | None`, `mark_done(story_id)`. 둘 다 async.
- `community`가 어댑터로 구현(Story 소유·상태 전이 `pending→reading→done`). **배선은 합성 루트 `aria/app.py`**.
- 근거: 사연은 저볼륨이고 "다음 pending 하나 claim"이 이벤트 큐보다 자연스러움. 게시판 쓰기는 community, 낭독 소비는 chat이 포트로.

> **포트를 소비자(chat) 쪽에 두지 않는 이유.** 독립성 계약은 **양방향**이다 — 포트를 `chat.application`에 두면 구현자인 community가 그것을 import해야 하고, 그 순간 `community ↛ chat`이 깨진다(import-linter로 실측 확인). 그래서 계약이 양쪽 밖, 즉 커널에 산다. `EventBusPort`와 같은 자리이며 "컨텍스트 간 경유는 `common`(이벤트/포트)"이라는 규약 그대로다.

> **합성 루트는 `common`이 아니라 `aria/app.py`다.** common은 컨텍스트를 import할 수 없으므로(`common-kernel-purity`) 거기서 조립할 수 없다.

> **DTO는 community의 `Story`가 아니다.** 도메인 객체를 그대로 넘기면 chat이 community 타입을 알게 된다. 낭독에 필요한 것만 담은 `PendingStory`를 common에 두고 어댑터가 변환한다 — `PersonaLLMPort`가 `Message`/`LLMResult`를 두고 OpenAI 타입과 매핑하는 것과 같다.

> **claim은 원자적이어야 한다.** 인스턴스가 여럿이면 같은 사연을 두 번 읽을 수 있다. `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1` 후 상태 전이(표준 큐 claim 패턴). `(persona_id, status)` 인덱스가 이를 받친다.

---

## 포트

- `EventBusPort`(`aria.common.eventbus`) — Kafka publish(FastStream 어댑터). 도메인/애플리케이션은 Kafka를 모름. 소비는 워커 진입점이 생길 때 추가.
- `StoryFeedPort`(`aria.common.story_feed`) — 위 사연 소비. community가 구현, chat이 소비, `aria/app.py`가 배선.

## 미확정

- DLQ 재시도 정책 수치, 스키마 버저닝 방식, 파티션 수
