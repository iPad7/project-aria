# 데이터베이스 명세

영구 데이터 스키마. 휘발 상태(세션·큐·토픽)는 Redis이며 여기 없음. ERD 다이어그램은 아직 없다 — 아래 표가 단일 소스다.

## 개요

- **DB 2개**: `aria` DB(앱 모놀리스 — identity·persona·community·chat·wallet) / `payments` DB(별도 서비스 — payment·outbox). 두 DB 간 FK 없음, **Kafka 이벤트로 연결**.
- ORM: **SQLModel 겸용**(도메인 모델 ≒ 영속 엔티티), 마이그레이션 **Alembic**.
- 규약: PK `id UUID`(**UUIDv7**, 앱 생성 — 시간순 정렬로 인덱스 지역성 + 전역 유일 + 비열거). 1:1 테이블은 FK가 PK. 시각은 `timestamptz`. 문자열 길이는 레거시 계승.
- 컨텍스트 경계: 테이블은 소유 컨텍스트에 속함. 크로스컨텍스트는 **읽기(read model)** 로만(직접 FK 조인 지양).

---

## identity (aria DB)

### user
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| username | varchar(150) | unique, not null |
| email | varchar(254) | unique, nullable |
| password_hash | varchar(255) | not null |
| nickname | varchar(50) | not null |
| profile_image_url | varchar(512) | nullable |
| is_staff | bool | default false (관리자) |
| created_at / updated_at | timestamptz | |

> **결정**: 크레딧 잔액은 여기 없다 → `wallet.wallet`으로 분리. 레거시도 이미 `UserWallet`·`CashLog`를 별도 모델로 두고 있었으므로(`User`에 credit 필드는 없었다), 새로 쪼갠 것이 아니라 **이미 분리돼 있던 것을 컨텍스트 경계로 승격**한 것이다.

---

## persona (aria DB) — 레거시 `influencers` 정규화 계승

### persona  *(레거시 Influencer)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| name | varchar(100) | unique, not null — 스트리머 식별자 = `PersonaLLMPort`의 `persona_id` |
| age | int | nullable |
| gender | varchar(10) | |
| mbti | varchar(10) | |
| job | varchar(100) | |
| audience_term | varchar(50) | 시청자 애칭 |
| origin_story | text | |
| created_at / updated_at | timestamptz | |

### core_value / persona_core_value  *(M:N + 우선순위)*
| core_value | | |
|---|---|---|
| id | uuid | PK |
| value_name | varchar(50) | unique, not null |

| persona_core_value | | |
|---|---|---|
| id | uuid | PK |
| persona_id | uuid | FK→persona, cascade |
| core_value_id | uuid | FK→core_value |
| priority | int | not null (정렬) |
| — | — | unique(persona_id, core_value_id) |

### communication_style / moral_compass / personality_trait  *(각 1:1 persona)*
| 테이블 | PK | 주요 컬럼 |
|---|---|---|
| communication_style | persona_id (FK) | tone, sentence_length, question_style, directness(int 1~5), empathy_expression |
| moral_compass | persona_id (FK) | standard, rule_adherence, fairness |
| personality_trait | persona_id (FK) | energy_direction, emotional_processing, judgment_decision, interpersonal_attitude, openness, conscientiousness, emotional_stability, social_sensitivity, risk_preference, time_orientation |

### tts_settings  *(1:1 persona, ElevenLabs)*
| 컬럼 | 타입 | 비고 |
|---|---|---|
| persona_id | uuid | PK, FK→persona, cascade |
| engine | varchar(50) | default 'elevenlabs' |
| voice / voice_name / model | varchar(100) | |
| stability / similarity / style | float | 0.0~1.0 |
| speaker_boost / auto_play | bool | |
| streaming_delay / tts_delay / chunk_size | int | ms·개 |
| sync_mode | varchar(20) | real_time/after_complete/chunked |
| updated_at | timestamptz | |

---

## community (aria DB) — 방송국

### story  *(사연 게시판)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| persona_id | uuid | FK→persona, cascade, not null |
| author_id | uuid | FK→user, nullable, **on delete set null** (탈퇴해도 글 유지) |
| title | varchar(200) | not null |
| content | text | not null |
| is_anonymous | bool | default true |
| relationship_stage | varchar(50) | nullable |
| nickname | varchar(50) | nullable |
| status | varchar(10) | default 'pending' — pending/reading/done (idle 낭독 상태) |
| created_at | timestamptz | |
| — | — | index (persona_id, created_at desc), (persona_id, status) |

> `chat`의 idle이 `status='pending'` 사연을 **읽기 포트/이벤트**로 소비(→ 이벤트 명세서에서 확정). `community`가 소유.

### like  *(좋아요)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| persona_id | uuid | FK→persona, cascade |
| user_id | uuid | FK→user, cascade |
| created_at | timestamptz | |
| — | — | unique(persona_id, user_id) |

### rankings (열혈순위) — **파생 read model, 테이블 없음**
`SELECT persona_id, SUM(amount) FROM donation GROUP BY persona_id` 형태로 `wallet.donation`에서 집계.

---

## chat (aria DB)

### chat_room
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| persona_id | uuid | FK→persona |
| host_id | uuid | FK→user (호스트/관리자) |
| name | varchar(255) | not null (방송 제목) |
| description | text | nullable |
| thumbnail_url | varchar(512) | nullable |
| status | varchar(10) | default 'pending' — pending/live/finished |
| hls_url | varchar(512) | nullable (HLS 송출) |
| created_at | timestamptz | |
| closed_at | timestamptz | nullable |

### chat_message_log  *(채팅 로그, 영구)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| room_id | uuid | FK→chat_room, cascade |
| sender_id | uuid | FK→user, nullable, set null |
| content | text | not null |
| created_at | timestamptz | index (room_id, created_at) |

> 실시간 전달은 Redis pub/sub. 이 테이블은 영구 로그.

### chat_room_log  *(입장/퇴장, P2)*
| id | uuid PK | · room_id FK · user_id FK · action varchar(enter/exit) · timestamp |

---

## wallet (aria DB)

### wallet  *(1:1 user, 잔액)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| user_id | uuid | PK, FK→user, cascade |
| credit_balance | int | not null, default 0 (check ≥ 0) |
| updated_at | timestamptz | |

### credit_transaction  *(원장, append-only)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | FK→user |
| delta | int | not null (+지급 / −사용) |
| type | varchar(20) | purchase / donation / refund |
| ref_id | varchar(64) | nullable (payment_id·donation_id) |
| idempotency_key | varchar(64) | nullable, unique (멱등 지급) |
| created_at | timestamptz | index (user_id, created_at) |

### donation  *(후원=슈퍼챗 기록, 랭킹 소스)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| persona_id | uuid | FK→persona |
| room_id | uuid | FK→chat_room, nullable |
| donor_id | uuid | FK→user, nullable, set null |
| amount | int | 크레딧, not null |
| message | text | nullable |
| created_at | timestamptz | index (persona_id, created_at) |

> **결정**: donation을 wallet 컨텍스트에(크레딧 spend 기록). `community` 랭킹·`streaming` 표시가 읽음. 지급/사용의 정합은 `credit_transaction` 원장이 담당(잔액은 materialized).

---

## payments (**payments DB — 별도**)

> aria DB와 FK 없음. `user_id`는 논리 참조. wallet과는 Kafka 이벤트(`credit-purchase-confirmed`)로만 연결.

### payment  *(결제 saga 상태)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | 논리 참조 |
| provider | varchar(20) | 'toss' |
| provider_payment_key | varchar(200) | nullable |
| amount_krw | int | 결제 금액 |
| credits | int | 지급 예정 크레딧 |
| status | varchar(20) | pending / confirmed / refund_pending / refunded / failed |
| idempotency_key | varchar(64) | unique (webhook 멱등) |
| created_at / updated_at | timestamptz | |

### outbox  *(transactional outbox)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| aggregate_id | varchar(64) | payment.id |
| event_type | varchar(50) | credit-purchase-confirmed 등 |
| payload | jsonb | 이벤트 본문 |
| status | varchar(10) | pending / published |
| created_at | timestamptz | index (status, created_at) |
| published_at | timestamptz | nullable |

---

## 관계 요약

- user 1—1 wallet · user 1—N (story.author, donation.donor, chat_message_log.sender, credit_transaction)
- persona 1—1 (communication_style, moral_compass, personality_trait, tts_settings) · 1—N (persona_core_value, story, like, donation, chat_room)
- chat_room 1—N (chat_message_log, chat_room_log, donation)
- payments DB(payment, outbox)는 독립 — 이벤트로만 연결

## 원칙

- **SQLModel 겸용**(Lite): 도메인 복잡도가 커지면 국소적으로 엔티티 분리.
- **Alembic** 마이그레이션(Django migration 대체).
- **휘발 상태는 Redis**: `StreamSession`·요청/응답 큐·토픽 스레드는 DB에 넣지 않음(수평확장). 레거시 인메모리/클래스변수의 Redis 외부화.
- **레거시 정리**: `chat.Story` vs `influencers.Story` 중복 → `community.story` 단일. `StreamerTTSSettings` vs `InfluencerTTSSettings` → `persona.tts_settings` 단일.

## 미확정

- 크레딧 소수/환불 부분취소 정책, `credit_transaction` 잔액 재구성 검증 주기
