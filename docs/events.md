# events

느리고 비싼 단계(LLM 생성 · 미디어 합성)와 크로스서비스 통신(payments↔wallet)을 위한 **durable 이벤트**. 전체 구조는 `docs/architecture.md`.

> **채팅 팬아웃과 혼동 금지.** 채팅 브로드캐스트는 Redis **pub/sub**(유실 허용). 아래는 durable **Kafka** seam.

## Kafka + FastStream

durable · at-least-once · consumer group · 리플레이가 필요. **FastStream**이 consumer/producer·직렬화·테스트를 얹는다(`EventBusPort`(`aria.common.eventbus`) 뒤 어댑터). Redis는 state + pub/sub 전담.

## 파이프라인 (코어 루프)

```
[chat 수신] ─선별→ Kafka: aria.chat.response-requested
                      → generation-worker: PersonaLLMPort.generate ─HTTP→ vLLM
                         → Kafka: aria.streaming.response-generated
                            → media-worker: TTS + 클립 + ffmpeg → HLS(CDN)
                               → Redis pub/sub: 시청자 타임라인 splice 알림
```

## payments → wallet (outbox)

```
payments: 결제확정 + outbox 로우            [로컬 트랜잭션, payments DB]
   → outbox relay ─publish→ Kafka: payments.credit-purchase-confirmed
      → wallet: 크레딧 지급 (멱등)           [로컬, aria DB]
환불 → Toss cancel(외부, N일) → webhook → payments emit → wallet 회수(보상)
```

## 이벤트 (초안)

| 이벤트 | 토픽 | 키 | 페이로드(초안) |
|---|---|---|---|
| ResponseRequested | `aria.chat.response-requested` | room_id | persona_id, selected_comment, context, msg_id |
| ResponseGenerated | `aria.streaming.response-generated` | room_id | text, emotion, model_version, msg_id |
| CreditPurchaseConfirmed | `payments.credit-purchase-confirmed` | user_id | payment_id, user_id, credits, idempotency_key |

## 전달 의미론

- **at-least-once**: consumer group + 처리 후 커밋. 멱등성으로 중복 흡수(`msg_id`/`seq`/`idempotency_key`).
- **outbox**: 결제확정과 이벤트 발행을 로컬 트랜잭션으로 원자화(이중발행/유실 방지).
- **DLQ**: N회 실패 시 `*.dlq` 토픽으로 (정책 미확정).

## 미확정

- superchat 우선순위(별도 토픽 vs 우선순위 필드)
- DLQ/재시도 수치, 이벤트 스키마 버저닝, 파티션 수
