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
| persona_id | uuid | not null, **인덱스만**(cross-context FK 없음) |
| author_id | uuid | nullable, 인덱스만 — 탈퇴 시 앱이 None으로 (탈퇴해도 글 유지) |
| title | varchar(200) | not null |
| content | text | not null |
| is_anonymous | bool | default true |
| relationship_stage | varchar(50) | nullable |
| nickname | varchar(50) | nullable |
| status | varchar(10) | default 'pending' — pending/reading/done (idle 낭독 상태) |
| created_at | timestamptz | |
| — | — | index (persona_id, created_at desc), (persona_id, status) |

> `chat`의 idle이 `status='pending'` 사연을 **읽기 포트**로 소비(`docs/events.md`에서 확정). `community`가 소유.

> **cross-context FK를 걸지 않는다.** `persona_id`·`author_id`는 다른 컨텍스트의 엔티티를 가리키지만 불투명 UUID일 뿐이고, 참조 무결성은 애플리케이션 규칙으로 지킨다 — 컨텍스트 독립을 물리 스키마까지 관철하기 위해서다(`docs/architecture.md`의 상호작용 규약). 아래 chat·wallet 절의 FK 표기도 같은 규약을 따라야 하며, 각 슬라이스 구현 시 정정한다.

### like  *(좋아요)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| persona_id | uuid | FK→persona, cascade |
| user_id | uuid | FK→user, cascade |
| created_at | timestamptz | |
| — | — | unique(persona_id, user_id) |

### rankings (열혈순위) — **파생 read model, 테이블 없음**

방송국 페이지가 보여주는 것은 **그 페르소나의 후원자 순위**다(FR-STATION-6). `wallet_donation`에서 집계한다:

```sql
SELECT donor_id, SUM(amount), COUNT(*)
FROM wallet_donation
WHERE persona_id = ? AND donor_id IS NOT NULL
GROUP BY donor_id
ORDER BY SUM(amount) DESC, MIN(created_at) ASC
LIMIT ?
```

> **이전 판은 `GROUP BY persona_id`로 적혀 있었다.** 그건 "어느 스트리머가 제일 많이 받았나"라는 전체 리더보드이고, "**방송국은** 후원 랭킹을 표시한다"는 요구사항과 다른 화면이다. C-3에서 후원자 순위로 정정했다. 페르소나 리더보드가 필요해지면 같은 테이블에서 따로 뽑는다.

> **`donor_id IS NULL`은 제외한다.** 익명 후원은 서로 다른 사람의 것이 섞여 있어, 한 줄로 합치면 실제로 그만큼 후원한 사람이 없는 1위가 만들어진다.

> **동점은 먼저 후원한 사람이 앞이다.** 정렬 기준이 금액 하나뿐이면 같은 금액끼리 순서가 실행마다 달라져 화면이 이유 없이 흔들린다.

> **테이블을 두지 않는 이유.** 후원은 저볼륨이고 집계는 `(persona_id, donor_id)` 인덱스 하나로 충분하다. read model 테이블을 두면 갱신 누락이라는 정합성 문제를 새로 만드는데, 얻는 것이 그만큼 크지 않다. 대신 조회 앞에 TTL 캐시를 둔다(좋아요 수와 같은 데코레이터 방식) — 새 후원이 순위에 반영되기까지 최대 TTL만큼 늦는 것은 감수한다. 무효화 훅을 만들자고 차감 경로에 캐시 의존을 끼워 넣으면 결제 트랜잭션이 Redis 장애에 묶인다.

> **금액은 wallet, 이름은 identity, 화면은 community.** 컨텍스트끼리 import하지 않으므로 계약이 커널에 산다 — `common.ranking`의 `DonationRankingPort`(wallet 구현)와 `common.user_directory`의 `UserDirectoryPort`(identity 구현). 배선은 합성 루트(`docs/events.md`).

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

> `user_id`·`persona_id`·`room_id`는 **FK가 아니다** — 위 community 절과 같은 규약(불투명 UUID + 인덱스만).

### wallet_wallet  *(1:1 user, 잔액)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| user_id | uuid | PK (대리키 없음 — 사용자당 지갑 하나) |
| credit_balance | int | not null, default 0, **check ≥ 0** |
| created_at / updated_at | timestamptz | |

### wallet_credit_transaction  *(원장, append-only)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | index (아래 복합 인덱스 선두) |
| delta | int | not null (+지급 / −사용) |
| type | varchar | purchase / grant / donation / refund |
| ref_id | varchar(64) | nullable (payment_id·donation_id) |
| idempotency_key | varchar(64) | nullable, **unique** (멱등 지급의 유일한 관문) |
| created_at / updated_at | timestamptz | `ix_wallet_credit_transaction_user_created` (user_id, created_at DESC) |

### wallet_donation  *(후원=슈퍼챗 기록, 랭킹 소스)*
| 컬럼 | 타입 | 제약/비고 |
|---|---|---|
| id | uuid | PK |
| persona_id | uuid | 복합 인덱스 선두 |
| room_id | uuid | nullable, index |
| donor_id | uuid | nullable, index (탈퇴해도 기록은 남음) |
| amount | int | 크레딧, not null (도메인 > 0) |
| message | varchar | nullable |
| created_at / updated_at | timestamptz | `ix_wallet_donation_persona_created` (persona_id, created_at DESC) |
| — | — | `ix_wallet_donation_persona_donor` (persona_id, donor_id) — 열혈순위 집계 |

> **결정**: donation을 wallet 컨텍스트에(크레딧 spend 기록). `community` 랭킹·`streaming` 표시가 읽음. 지급/사용의 정합은 `credit_transaction` 원장이 담당(잔액은 materialized).

> **인덱스가 둘인 이유.** 방송국의 후원 목록은 최신순 스캔(`persona_id, created_at DESC`)이고, 열혈순위는 한 페르소나의 로우를 `donor_id`로 묶는 집계(`persona_id, donor_id`)다. 접근 형태가 달라 하나로 겸할 수 없다. `amount`를 뒤에 붙여 커버링 인덱스로 만드는 선택지도 있지만, 후원 로우 폭이 좁아 힙 접근이 싸므로 실측 전에는 넣지 않는다.

> **잔액과 원장을 한 트랜잭션에 갱신한다.** 트랜잭션 경계를 서비스가 쥘 수 없어서(서비스는 Session을 모른다) **원자적 단위 자체를 포트 연산으로** 표현했다 — `WalletRepository.apply(entry, donation=…)` 한 번의 호출이 원장 append + 잔액 갱신 + 후원 기록을 함께 한다.

> **차감은 조건부 UPDATE 한 문장이다.** `UPDATE … SET credit_balance = credit_balance − x WHERE user_id = ? AND credit_balance >= x`. 잔액 확인과 차감이 갈라지면 동시 요청 두 개가 같은 잔액을 보고 둘 다 통과한다. 0행이 갱신되면 그것이 곧 '잔액 부족'이다. 실측: 잔액 300에 100원 후원 8개 동시 시도 → 정확히 3건 성공, 잔액 0. `type`은 `SELECT … FOR UPDATE` 대신 이 방식이라 잠금 대기가 없다.

> **멱등 지급은 유일 제약이 강제한다.** 원장을 **먼저** insert하고 제약 위반이면 잔액을 건드리기 전에 빠져나온다. 앱 레벨의 "이미 있나?" 검사로는 동시 요청 두 개를 막을 수 없다(community의 좋아요와 같은 방식). 실측: 같은 멱등키 8회 동시 지급 → 원장 1줄, 잔액 1회분.

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
