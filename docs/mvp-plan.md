# mvp-plan

원칙: **앱(레거시 기능 재현) 먼저**, ML/LLMOps는 포트 stub으로 두고 이후.

이 문서는 **무엇을 만들지(기능 로드맵)** 를 다룹니다. **어떤 순서로 전달했는지**는 GitHub umbrella 이슈로 추적하며, 실행 슬라이스는 기능 축을 가로질러 아래 로드맵 Phase와 1:1로 대응하지 않습니다.

## 전달 현황 (umbrella 이슈)

- [x] 기반 슬라이스 (#8) — 공통 커널 · identity(회원가입·로그인·JWT) · persona(CRUD·소유권) · chat 조율 골조(Redis 분산락·우선순위 선점·펜싱)
- [x] 실시간 종단 + 인프라 (#15) — docker-compose·Alembic 실체화 · WebSocket 전송(첫 프레임 인증) · Redis pub/sub 팬아웃(서버 2대 수평확장 실증)
- [ ] 진행 중 (Phase 3, #21 — 순서 확정) — **A** 실제 생성 **완료** → **B** community 슬라이스(사연·좋아요) **완료** → **C** superchat: C-1 wallet **완료** · C-2 후원 종단 **완료** · C-3 열혈순위 **완료** · C-4 durability — C-4-1 배관(Kafka 어댑터·generation-worker·생성 이동) **완료** · C-4-2 내구성 마감(토픽별 전달 의미론·`msg_id` 멱등·DLQ·수평확장 실증) **완료** → **슬라이스 C 완료** → 이후 **ML 플랫폼**(SFT·평가). 근거: 쓸 수 있는 걸 먼저 만들어 데이터·기준선을 확보한 뒤 자체 모델로 전환. WS 구독 공유 최적화는 부하가 실측되기 전이라 보류.
  - **A-1** OpenAI 호환 어댑터로 `PersonaLLMPort` 실체화(#23) + 폴백 합성(#32) — 완료
  - **A-2** 자체 vLLM 서빙 — 완료. A.X-4.0-Light를 4비트 AWQ로 직접 양자화(4.6GB)해 RTX 5050에서 서빙하고, aria → LAN → 자체 vLLM 종단을 검증했습니다. 추론 서빙은 GPU 하드 경계라 별도 repo — [project-aria-inference](https://github.com/iPad7/project-aria-inference).
  - A-1의 남은 절반(페르소나별 시스템 프롬프트 해석)은 #22에서 계속 추적합니다. 운영 경로에서는 페르소나 구별이 포트 뒤 멀티-LoRA로 일어나므로, 이건 OpenAI 경로와 자체 모델 도입 전까지의 수단입니다.
  - **기준선은 A-1의 OpenAI로 유지합니다.** 8GB VRAM 때문에 택한 4비트 양자화가 나중에 SFT 모델을 평가하는 잣대가 되어선 안 되기 때문입니다. A-2는 "자체 GPU에서 자체 모델이 돈다"는 실증입니다.

## 기능 로드맵

### Phase 0 — 골격

- [x] uv 프로젝트, Hexagonal-Lite 트리, import-linter 경계
- [x] `PersonaLLMPort` 계약(A/B 경계) 고정
- [x] FastAPI `/health`

### Phase 1 — 도메인 + 채팅 골조

- [x] persona 도메인(CRUD·소유권) — `name`/`tagline`/`description` 수준
- [ ] persona 레거시 스키마 계승 — `core_value`(M:N) · `communication_style` · `moral_compass` · `personality_trait` · `tts_settings` 미구현(`docs/data-model.md`에 설계만). 페르소나별 프롬프트 해석(#22)과 방송국 프로필(FR-STATION-1)이 이걸 필요로 함
- [x] 방(Room) — 개설·상태(pending/live/finished)·라이브 유일성. 채팅·후원·WS가 라이브 방에서만 된다. idle 루프와 미디어 송출(`hls_url`)의 전제
- [ ] 토픽스레드 · MediaPacket/seq · 응답선별
- [x] WebSocket 채팅 수신·브로드캐스트 (Redis pub/sub 백플레인)
- [x] 세션/큐·조율 상태 Redis 외부화

### Phase 2 — 생성 파이프라인

- [ ] 댓글 선별 → `PersonaLLMPort` 호출 → 응답 — 호출·응답 경로는 실추론까지 배선 완료, **선별(FR-GEN-1·2) 미구현**. C-4-1에서 생성이 generation-worker로 빠졌다(api는 요청만 발행, 응답은 Redis pub/sub으로 흐름)
- [x] inference 어댑터 + OpenAI fallback (포트 뒤) — `OpenAICompatLLM`(#23) · `FallbackPersonaLLM`(#32). 기본값은 `stub`이라 키 없는 로컬·CI는 그대로 통과
- [x] idle 자동 진행(사연·자율발화, FR-IDLE-1·2·3) — idle 워커가 라이브 방을 훑어 사연을 읽거나 자율발화한다. **FR-STATION-4 → FR-IDLE-2 사슬 연결됨** — `StoryFeedPort`(#42)가 드디어 소비자를 얻었다

### Phase 3 — 미디어 송출 (서버합성 HLS)

- [ ] TTS 어댑터
- [ ] 합성 워커: 클립 선택 + TTS mux → ffmpeg → HLS 세그먼트
- [ ] idle 루프 타임라인 + 응답 splice, CDN

### Phase 4 — 재현 마무리

- [ ] 인증/관리자 — identity 인증은 기반 슬라이스(#8)에서 선구현. 관리자 권한(`is_staff` → JWT `staff` 클레임 → `require_staff`)은 C-1에서 선구현, 관리자 화면·잔여 인증 흐름 남음
- [ ] 후원(토스) — wallet · payments 경계. **wallet 컨텍스트(잔액·원장·후원 기록)는 C-1, 후원 종단(FR-PAY-3·FR-GEN-6)은 C-2에서 완료** — 결제 확정 이벤트가 붙을 지급 경로(`WalletService.grant(type=PURCHASE)`)까지 열어 둠. 남은 것은 payments 서비스와 outbox relay

## 이후 (ML 플랫폼 / 확장)

- [ ] ML 플랫폼: 데이터셋 → SFT → DPO → 평가 → 레지스트리
- [ ] 관측성 1급화, 클립/숏폼 자동생성
