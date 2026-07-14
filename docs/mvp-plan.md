# mvp-plan

원칙: **앱(레거시 기능 재현) 먼저**, ML/LLMOps는 포트 stub으로 두고 이후.

## Phase 0 — 골격 (현재)
- [x] uv 프로젝트, Hexagonal-Lite 트리, import-linter 경계
- [x] `PersonaLLMPort` 계약(A/B 경계) 고정
- [x] FastAPI `/health`

## Phase 1 — 도메인 + 채팅 골조
- [ ] 도메인 모델: 페르소나(레거시 스키마 계승), 토픽스레드, MediaPacket/seq, 응답선별
- [ ] WebSocket 채팅 수신·브로드캐스트 (Redis pub/sub 백플레인)
- [ ] 세션/큐 상태 Redis 외부화

## Phase 2 — 생성 파이프라인
- [ ] 댓글 선별 → `PersonaLLMPort` 호출 → 응답
- [ ] inference 어댑터 + OpenAI fallback (포트 뒤)
- [ ] idle 자동 진행(사연·자율발화)

## Phase 3 — 미디어 송출 (서버합성 HLS)
- [ ] TTS 어댑터
- [ ] 합성 워커: 클립 선택 + TTS mux → ffmpeg → HLS 세그먼트
- [ ] idle 루프 타임라인 + 응답 splice, CDN

## Phase 4 — 재현 마무리
- [ ] 인증/관리자, 후원(토스)

## 이후 (ML 플랫폼 / 확장)
- [ ] ML 플랫폼: 데이터셋 → SFT → DPO → 평가 → 레지스트리
- [ ] 관측성 1급화, 클립/숏폼 자동생성
