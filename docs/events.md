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

| 토픽 | producer | consumer group | key | 페이로드 | 상태 |
|---|---|---|---|---|---|
| `aria.chat.response-requested` | chat api | `generation-workers` | room_id | msg_id, room_id, persona_id, source, prompt, requested_at | **구현됨** (C-4-1) |
| `aria.chat.superchat-requested` | chat api (후원 수신) | `generation-workers` | room_id | 위와 같은 모양 (`source="superchat"`) | **구현됨** (C-4-1) |
| `aria.streaming.response-generated` | generation-worker | **broadcaster** (별도 repo) | room_id | msg_id, room_id, persona_id, text, emotion, model_version, generated_at | 미구현 |
| `<topic>.dlq` | generation-worker | (사람) | 원본과 같음 | original_topic, failed_at, error, original | **구현됨** (C-4-2) |

> **두 요청 토픽의 페이로드는 같은 모양이다.** 원래 표는 `selected_comment`/`donor`처럼 서로 다른 필드를 적어 두었지만, 워커에게 필요한 것은 결국 "어느 방의 어느 페르소나가 무엇에 답하는가"뿐이라 하나의 `GenerationRequest`로 합쳤다. 후원 금액·메시지는 문구로 풀려 `prompt`에 들어간다. (`selected_comment`가 사라진 또 다른 이유: 선별(FR-GEN-1·2)이 아직 없어 실제로 실리는 것은 사용자가 보낸 원문이다.)

> **지금 응답은 Kafka로 돌아오지 않는다.** 브로드캐스터가 생기기 전까지 generation-worker는 생성한 답변을 곧바로 **Redis pub/sub 방 채널**로 발행하고, api 프로세스의 WS 연결들이 그것을 받는다. 팬아웃이 이미 프로세스 경계를 넘고 있었기 때문에 클라이언트는 아무것도 바뀌지 않았다.
>
> **`response-generated`의 소비자는 aria 밖이다.** 브로드캐스터는 별도 repo이고, Kafka가 그 경계다 — 컨텍스트 간 경유를 common으로 하듯, repo 간 경유는 토픽으로 한다.

> 미디어 합성 완료 후 시청자 알림(타임라인 splice)은 **Redis pub/sub**(아래). Kafka 토픽 아님.

> **superchat이 별도 토픽인 이유가 바뀌었다.** 원래 근거는 "Kafka는 파티션 내 우선순위가 없으니 워커가 superchat 토픽을 먼저 drain한다"는 표준 패턴이었다. 그런데 우리에겐 이미 `ResponseCoordinator`가 있고, 그건 **진행 중인 생성까지 선점**할 수 있어 drain 순서보다 강하다 — 큐를 아무리 잘 골라도 이미 돌고 있는 채팅 응답은 멈추지 못하지만 선점은 멈춘다. 그래서 워커는 두 토픽을 나란히 구독하고 순서 다툼은 코디네이터에 맡긴다.
>
> 그럼에도 토픽을 합치지 않는 이유는 남아 있다: **소비 지연·재처리·DLQ를 후원 쪽만 따로 다룰 수 있어야 한다**(C-4-2). 돈이 오간 요청과 그냥 채팅은 실패했을 때 대응이 다르다.

### payments ↔ wallet

| 토픽 | producer | consumer group | key | 페이로드 |
|---|---|---|---|---|
| `payments.credit-purchase-confirmed` | payments (outbox relay) | wallet-workers | user_id | payment_id, user_id, credits, idempotency_key, confirmed_at |
| `payments.credit-refunded` | payments (outbox relay) | wallet-workers | user_id | payment_id, user_id, credits, refunded_at |

---

## Redis pub/sub 채널 (휘발)

| 채널 | publisher | 용도 |
|---|---|---|
| `chat:room:{room_id}` | chat api/worker | 채팅·응답·**후원(superchat)** 메시지 전 인스턴스 팬아웃 |
| `stream:room:{room_id}` | broadcaster | 방송 상태 알림(송출 시작·중단), 후원 **영상 오버레이**, 큐 상태 |

프레임 종류는 `type` 필드로 구분한다: `message` · `reply` · `superchat`. `reply`는 `source`(`chat`/`superchat`/`story`/`idle`)를 함께 실어, 클라이언트가 감사 응답과 일반 응답을 구분할 수 있게 한다.

---

## 전달 의미론

**토픽마다 다르다.** 하나로 통일하지 않은 것이 C-4-2의 핵심 결정이고, 토픽을 갈라 둔 값이 여기서 나온다.

| 토픽 | ack | 의미론 | 잃으면 |
|---|---|---|---|
| `aria.chat.response-requested` | `ACK_FIRST` (핸들러 전 커밋) | **at-most-once** | "AI가 그 말엔 답을 안 했네" — 채팅 응답은 시간이 지나면 가치를 잃는다 |
| `aria.chat.superchat-requested` | `ACK` (핸들러 후 커밋) | **at-least-once** | 돈을 낸 사람이 아무 반응도 못 받는다 — 다른 무게의 사고다 |

> ⚠️ **이전 판은 "at-least-once"라고만 적혀 있었고 코드는 그렇지 않았다.** FastStream의 기본값이 `ACK_FIRST`(핸들러 **전** 커밋)라 실제로는 at-most-once였고, 생성 중 워커가 죽으면 메시지가 재시도 없이 사라졌다. C-4-1이 그 값을 명시하지 않고 기본값에 맡긴 결과이며, C-4-2에서 토픽별로 명시했다.

예외는 두 정책 모두에서 핸들러가 삼켜 DLQ로 보내므로, 둘의 실질적 차이는 **프로세스가 통째로 죽는 경우**다. 그게 정확히 구분하려던 경우다.

- **멱등**: `msg_id`를 Redis `SET NX`로 **claim**한다(생성) / `idempotency_key`(크레딧). "봤음" 표시가 아니라 claim이라는 점이 중요하다 — 잡고 → 처리하고 → **실패하면 놓는다**. 표시만 남기면 일시 실패가 영구 유실이 되어 at-least-once로 바꾼 의미가 사라진다. TTL(기본 1시간)은 재전달 창보다 길고, 사람이 DLQ를 보는 주기보다는 짧다. 워커가 DB를 모른다는 설계를 지키려고 Redis에 둔다 — 슬롯이 이미 Redis에 있으므로 새 의존도 아니다.
- **순서**: 같은 `key`(room_id·user_id)는 같은 파티션 → 순서 보장.
- **파티션 수는 코드가 선언한다**(`common/topics.py`, 파티션 3). 자동 생성에 맡기면 1개가 되어 워커를 몇 대 띄우든 하나만 일한다 — consumer group은 파티션 단위로 나눠 갖기 때문이다. **기존 토픽의 파티션은 늘리지 않는다**: 늘리면 키→파티션 매핑이 바뀌어 같은 방의 과거·미래 메시지가 다른 파티션에 가고 순서 보장이 그 지점에서 끊긴다. 운영자가 알고 하는 편이 낫다.
- **outbox** (payments): `payment` 상태변경 + `outbox` 로우를 **한 로컬 트랜잭션**에 기록 → relay가 `outbox`를 폴링/CDC로 Kafka 발행 → `published`로 마킹. 이중발행·유실 방지.
- **DLQ**: **재시도 0회**, 실패 즉시 `<topic>.dlq`. 원본 페이로드 + `original_topic`·`failed_at`·`error`를 함께 싣고, 키는 원본과 같아 DLQ에서도 방별 순서가 남는다. 사람이 보고 판단한다(후원이면 환불이든 재처리든).
  > **왜 재시도하지 않나.** ① 인프로세스 재시도는 파티션을 막는다(head-of-line blocking) — 같은 방의 뒤 메시지가 앞 메시지의 백오프를 기다린다. ② 30초 뒤에 성공한 응답은 이미 늦었다. at-least-once의 값은 "늦게라도 성공"이 아니라 **"조용히 사라지지 않는다"**에 있다. 자동 재시도로 한참 뒤에 감사 응답이 튀어나오는 것이 오히려 이상하다.
- **스키마 버저닝**: 페이로드에 `v` 필드. 지금은 `v: 1`이고, **모르는 버전은 추측하지 않고 DLQ로 보낸다** — 필드가 바뀐 메시지를 옛 코드가 반쯤 읽어 이상한 응답을 내보내는 것이 최악이다. `v`가 없는 페이로드는 1로 본다(C-4-2 이전에 발행돼 큐에 남아 있던 것). 소비자가 아직 우리 자신뿐일 때가 넣는 비용이 가장 싼 시점이라 미리 넣었다.

---

## 사연(Story) 소비 — **읽기 포트로 확정** (이벤트 아님)

idle이 사연을 읽는 흐름은 Kafka가 아니라 **동기 읽기 포트**로 한다.

- **`StoryFeedPort`는 `common/story_feed.py`에 둔다**: `claim_next_pending(persona_id) -> PendingStory | None`, `mark_done(story_id)`, `release(story_id)`. 전부 async.
- `community`가 어댑터로 구현(Story 소유·상태 전이 `pending→reading→done`).
- **소비자는 idle 워커이고, 배선도 거기서 한다**(`aria/workers/idle.py`). 이전 판은 "배선은 `aria/app.py`"라고 적어 두었으나, 이 포트를 쓰는 것은 api가 아니라 idle 루프다 — 합성 루트가 여럿이고 각자 필요한 것을 조립한다.
- 근거: 사연은 저볼륨이고 "다음 pending 하나 claim"이 이벤트 큐보다 자연스러움. 게시판 쓰기는 community, 낭독 소비는 chat이 포트로.

> **포트를 소비자(chat) 쪽에 두지 않는 이유.** 독립성 계약은 **양방향**이다 — 포트를 `chat.application`에 두면 구현자인 community가 그것을 import해야 하고, 그 순간 `community ↛ chat`이 깨진다(import-linter로 실측 확인). 그래서 계약이 양쪽 밖, 즉 커널에 산다. `EventBusPort`와 같은 자리이며 "컨텍스트 간 경유는 `common`(이벤트/포트)"이라는 규약 그대로다.

> **합성 루트는 `common`이 아니다.** common은 컨텍스트를 import할 수 없으므로(`common-kernel-purity`) 거기서 조립할 수 없다. 지금 합성 루트는 셋이다 — `aria/app.py`(api) · `aria/workers/generation.py` · `aria/workers/idle.py`. 각자 자기가 쓰는 포트만 배선한다.

> **DTO는 community의 `Story`가 아니다.** 도메인 객체를 그대로 넘기면 chat이 community 타입을 알게 된다. 낭독에 필요한 것만 담은 `PendingStory`를 common에 두고 어댑터가 변환한다 — `PersonaLLMPort`가 `Message`/`LLMResult`를 두고 OpenAI 타입과 매핑하는 것과 같다.

> **claim은 원자적이어야 한다.** 인스턴스가 여럿이면 같은 사연을 두 번 읽을 수 있다. `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1` 후 상태 전이(표준 큐 claim 패턴). `(persona_id, status)` 인덱스가 이를 받친다.

> **claim은 조회가 아니라 소비다 — 그래서 `release`가 필요하다.** 사연을 집은 뒤 발행에 실패하면 그 사연은 읽히지도 않은 채 `reading`에 갇힌다(시청자가 남긴 글이 조용히 사라진다). 실패 시 `reading → pending`으로 되돌린다. 같은 이유로 **idle 워커는 방별 락을 사연 claim보다 먼저 잡는다**: `ResponseCoordinator`가 나중에 중복 발화를 걸러 주지만, 그때는 이미 사연이 큐에서 빠져나온 뒤다.

> ⚠️ **`done`은 지금 "발행됐다"는 뜻이다.** 진짜 낭독 완료를 아는 것은 응답을 내보낸 generation-worker인데, 그 워커는 사연도 DB도 모른다(C-4의 경계). 그래서 idle 워커가 요청을 발행한 직후 `done`으로 옮긴다. 대가: 워커가 실패해 DLQ로 가면 읽히지 않은 사연이 done이 된다. 표시하지 않으면 읽은 사연이 전부 `reading`에 남아 게시판이 영원히 "낭독 중"으로 보이므로, 둘 중 덜 나쁜 쪽을 골랐다. 진짜 완료 신호는 미디어 송출이 붙어 "다 읽었다"가 정의될 때 생긴다.
>
> 프로세스가 통째로 죽어 `release`도 `mark_done`도 못 부른 사연은 `reading`에 남는다. 타임아웃 청소는 아직 없다 — C-4-2의 claim TTL과 같은 성격의 문제라 함께 다룬다.

---

## 후원(superchat) — **동기 포트로 확정** (이벤트 아님)

시청자가 크레딧으로 후원하면(FR-PAY-3) 차감·기록은 `wallet`이, 트리거와 감사 응답(FR-GEN-6)은 `chat`이 한다. 이 연결도 Kafka가 아니라 **동기 포트**다.

- **`SuperchatPort`는 `common/superchat.py`에 둔다**: `charge(donor_id, persona_id, amount, *, room_id, message, idempotency_key) -> SuperchatReceipt`.
- `wallet`이 어댑터로 구현(`WalletSuperchat`, `anyio.to_thread`로 sync 리포지토리 오프로드). **배선은 합성 루트 `aria/app.py`** — FastAPI `dependency_overrides`로 chat이 선언한 자리에 구현을 꽂는다.
- 실패는 `InsufficientCreditError`(`common.errors`) — wallet이 던지고 chat이 잡는다.

> **왜 이벤트가 아닌가.** 사연 낭독과 달리 후원은 **실패를 즉시 알아야 한다**. 차감이 실패했는데 감사 응답이 나가면 공짜 후원이 되고, 차감만 되고 아무 표시도 안 나가면 돈만 사라진다. 비동기로 흘려보내면 그 사이를 메울 방법이 없다.

> **순서가 계약이다.** ① 차감 → 실패하면 여기서 끝, 방송에는 아무 것도 안 나간다. ② 후원 표시 발행 — **감사 응답 여부와 무관하게 항상**. ③ 응답 슬롯 확보 → 잡히면 감사 응답 생성·발행. 차감이 성공한 뒤에는 슬롯을 못 잡아도 후원 자체는 성립한다.

> **예외가 컨텍스트가 아니라 커널에 있는 이유.** `InsufficientCreditError`는 wallet이 던지지만 chat이 잡고 payments도 환불 보상에서 마주친다. 컨텍스트끼리 서로 import하지 않으므로 공통 어휘는 `common.errors`에 산다.

---

## 열혈순위 — **동기 읽기 포트 둘로 확정** (이벤트 아님)

방송국은 그 페르소나의 후원자 순위를 보여준다(FR-STATION-6). 금액은 `wallet`이, 표시명은 `identity`가, 화면은 `community`가 갖고 있다. 셋을 잇는 것도 Kafka가 아니라 **동기 포트**다.

- **`DonationRankingPort`는 `common/ranking.py`에**: `top_donors(persona_id, *, limit) -> list[DonorRank]`. `wallet`이 구현(`WalletDonationRanking`).
- **`UserDirectoryPort`는 `common/user_directory.py`에**: `display_names(user_ids) -> dict[UUID, str]`. `identity`가 구현(`IdentityUserDirectory`).
- `community`의 `RankingService`가 둘을 합쳐 순위표를 만든다. **배선은 합성 루트 `aria/app.py`** — FastAPI `dependency_overrides`로 community가 선언한 자리에 두 구현을 꽂는다.

> **왜 이벤트가 아닌가.** 순위는 조회 시점의 질문이지 통지할 사건이 아니다. 이벤트로 하려면 community가 read model 테이블을 두고 후원 이벤트로 갱신해야 하는데, 그건 갱신 누락이라는 정합성 문제를 새로 만든다(`docs/data-model.md`). 집계는 인덱스 하나로 충분하고, 비용은 어댑터 쪽 TTL 캐시가 받는다.

> **왜 이 포트들만 sync인가.** `StoryFeedPort`·`SuperchatPort`는 소비자가 chat(async)이라 async였다. 이 둘의 소비자는 community의 HTTP 핸들러이고 그건 sync 함수다 — FastAPI가 스레드풀에서 돌리므로 블로킹 DB 호출이 이벤트 루프를 막지 않는다. async로 두면 구현이 `anyio.to_thread`로 sync 리포지토리를 다시 감싸야 하는데 얻는 게 없다. **포트의 색은 소비자가 정한다.**

> **이름 조회가 벌크인 이유.** 순위 한 줄마다 조회하면 N+1이다. 이름을 붙이자고 랭킹을 20배 느리게 만들 수 없으므로 계약 자체를 여러 건 단위로 못박았다.

> **탈퇴한 후원자는 순위에 남되 이름이 없다.** 이름이 없다고 줄을 빼면 아래 순위가 한 칸씩 올라가 실제 순위가 아니게 된다. 포트는 "찾은 것만" 돌려주고, 무엇을 대신 보여줄지는 화면이 정한다.

---

## 선점은 두 가지를 함께 해야 성립한다

`ResponseCoordinator`(Redis)의 우선순위 락은 **새 생성을 못 시작하게 막는 것**(`try_acquire`)과 **이미 돌던 생성의 결과를 버리는 것**(`still_holds`)이 둘 다 있어야 의미가 있다.

앞의 것만 있으면 선점당한 쪽이 생성을 끝내고 답변을 그대로 발행해 버린다 — 슈퍼챗 감사 응답과 밀려난 채팅 응답이 둘 다 나가서 우선순위가 장식이 된다. 생성은 외부 호출이라 중간에 취소할 수 없으므로, 취소 대신 **결과를 내보내기 직전에 아직 내 슬롯인지 확인**한다.

> **셋 다 워커에 있다**(C-4-1부터). `try_acquire`/`still_holds`/`release`는 요청 경로가 아니라 generation-worker의 것이다. 슬롯의 의미가 "지금 이 방에서 생성 중인 자"이기 때문이다 — 발행 시점에 잡으면 큐에서 기다리는 동안 슬롯을 쥐게 되어 대기 중인 요청이 실제 생성을 막는다.

---

## 생성은 요청 경로 밖에 있다 (C-4-1)

api는 요청을 접수하고 **표시할 것을 발행한 뒤** 생성을 큐에 맡기고 즉시 끝난다. 워커가 만든 응답은 Redis pub/sub 방 채널로 나가 그 방을 구독한 모든 연결에 도달한다.

- `POST /rooms/{id}/messages` → **202**, 응답 없음.
- `POST /rooms/{id}/superchats` → 200이되 `donation_id`·`balance_after`만. 차감이 동기라 그 둘은 즉시 확정된다.
- WS는 그대로다. 이미 구독으로 응답을 받고 있었기 때문에 **클라이언트 변경이 없다**.

> **표시 프레임 발행이 라우터에서 유스케이스로 올라갔다.** 전에는 WS 라우터가 발행했는데, 그 결과 ① HTTP로 보낸 메시지는 방에 나타나지 않았고(전송마다 동작이 갈렸다) ② 후원에서는 워커가 감사 응답을 먼저 발행해 "고맙습니다"가 후원 표시보다 앞서 나가는 순서 뒤집힘이 가능했다. 순서가 계약이라면 그 순서는 한 곳에 있어야 한다.

> **워커 진입점은 인바운드 어댑터다.** HTTP 라우터가 요청을 받아 애플리케이션 서비스를 부르듯 워커는 메시지를 받아 같은 일을 한다. 그래서 `EventBusPort`에 `subscribe`를 넣지 않았다 — 포트는 애플리케이션이 **밖을 부를 때** 필요한 것이고, 밖에서 안으로 들어오는 방향은 어댑터가 서비스를 직접 부른다. 합성 루트도 api와 별개다(`aria/workers/generation.py`).

> **워커는 DB를 모른다.** 슬롯(Redis)·생성(포트 뒤)·발행(Redis pub/sub)이 전부다. 후원 차감은 이미 요청 경로에서 끝났으므로 워커가 wallet을 알 이유가 없다.

---

## 포트

- `EventBusPort`(`aria.common.eventbus`) — Kafka publish(FastStream 어댑터 `common.kafka.KafkaEventBus`). 도메인/애플리케이션은 Kafka를 모름. **발행 전용이며 앞으로도 그렇다** — 아래 참조.
- `StoryFeedPort`(`aria.common.story_feed`) — 사연 소비. community가 구현, chat이 소비, `aria/app.py`가 배선.
- `SuperchatPort`(`aria.common.superchat`) — 후원 결제. wallet이 구현, chat이 소비, `aria/app.py`가 배선.
- `DonationRankingPort`(`aria.common.ranking`) — 열혈순위 집계. wallet이 구현, community가 소비, `aria/app.py`가 배선.
- `UserDirectoryPort`(`aria.common.user_directory`) — 표시명 벌크 조회. identity가 구현, community가 소비, `aria/app.py`가 배선.
- `TracingPort`(`aria.common.tracing`) — LLM 관측(NFR-OBS-2). Langfuse 어댑터는 `common.langfuse_tracing`, **기본은 `NoOpTracing`**. 서비스가 바깥 span을, `TracedPersonaLLM`이 안쪽 generation을 만든다.
- `PersonaProfilePort`(`aria.common.persona_profile`) — 페르소나 인격(말투·가치관). persona가 구현, chat이 소비, `aria/workers/generation.py`가 배선. **`persona_id`를 인격으로 해석하는 유일한 경로다** — 그전까지 chat은 id를 받아 아무도 해석하지 않았다.

## 미확정

- payments outbox relay(폴링 vs CDC), 운영 파티션·복제 수(IaC), DLQ 재처리 도구
  > DLQ 재시도 정책·스키마 버저닝·개발 파티션 수는 C-4-2에서 확정했다(위 "전달 의미론").
