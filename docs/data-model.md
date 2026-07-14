# data-model

영구 데이터(PostgreSQL, SQLModel 겸용 + Alembic). 휘발 상태(세션·큐·토픽)는 Redis이며 여기 없음.

> 페르소나 스키마는 레거시 `influencers` 앱에서 **정규화 설계를 계승**(살릴 자산). 나머지는 레거시 재현.

## 페르소나 애그리게이트 (계승)

레거시 `persona_loader`가 조립하던 구조. `domain`의 핵심.

```
Persona (Influencer)
  name, age, gender, mbti, job, audience_term, origin_story
  ├─ CoreValues        우선순위순 (다대다: Persona × Value)
  ├─ CommunicationStyle  tone, sentence_length, question_style, directness, empathy_expression
  ├─ MoralCompass        standard, rule_adherence, fairness
  └─ PersonalityTrait    energy_direction, emotional_processing, interpersonal_attitude, …
```

- `PersonaLLMPort`로 넘길 시스템 프롬프트는 이 애그리게이트에서 조립(레거시 `MASTER_PERSONA_PROMPT_TEMPLATE` 계승·정리).
- **모델 버전은 여기 없다** — persona는 "누구"이고, 어떤 sLLM 버전으로 서빙할지는 ML 플랫폼(레지스트리)의 관심사. `docs/architecture.md` 참고.

## 스트리밍 엔티티 (재현)

| 엔티티 | 핵심 필드 | 비고 |
|---|---|---|
| User | id, username, nickname, profile_image, credit | 인증(JWT), 후원 잔액 |
| Room | id, persona_id, title, status(pending/live/finished), hls_url | 방송. `hls_url`은 HLS 송출용 |
| ChatMessageLog | room_id, sender_id, content, created_at | 채팅 로그(영구). 실시간 전달은 pub/sub |
| Story | id, user_id, title, body, status(pending/reading/done) | 사연(idle 진행). 레거시 중복정의 정리 |
| Donation | id, user_id, room_id, amount, message, created_at | 후원 (크레딧 차감, 로컬 트랜잭션) |
| Wallet | user_id, credit_balance | 잔액. 모놀리스(핫패스). payments 이벤트로 지급, 후원으로 차감 |
| TTSSettings | persona_id, engine, voice, params… | 페르소나별. ElevenLabs 기본 |

> **payments 서비스는 별도 DB**(결제기록·outbox)를 소유한다. wallet(잔액)만 aria DB에. 둘은 Kafka 이벤트(`credit-purchase-confirmed`)로 연결 — `docs/events.md`.

## 원칙

- **SQLModel 겸용**(Lite): 도메인 모델 ≒ 영속 엔티티, 매핑 보일러플레이트 제거. 도메인 복잡도가 커지면 국소 분리.
- **마이그레이션 = Alembic**(Django migration 대체).
- **휘발 상태는 Redis**: `StreamSession`·큐·토픽 스레드는 DB에 넣지 않는다(수평확장). 레거시가 인메모리/클래스변수로 두던 것을 Redis로 외부화.
- 레거시 모델 중복(`chat.Story` vs `influencers.Story`)은 단일 `Story`로 통합.

## 미확정

- 크레딧/결제 원장 모델 상세
- 페르소나 에셋(idle/감정 클립) 메타데이터 저장 위치
