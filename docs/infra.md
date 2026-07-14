# 인프라 설계 (예상)

배포 토폴로지와 인프라 구성. 다이어그램은 `docs/infra.png`(첨부). 구조 근거는 `docs/architecture.md`.

## 배포 단위 요약

| 단위 | 런타임 | 확장 |
|---|---|---|
| aria (api / generation-worker / media-worker) | Python, 같은 이미지·다른 command | 워커 타입별 수평 |
| payments 서비스 | Python (별도 이미지·별도 DB) | 수평 |
| inference 서빙 | vLLM, **GPU** (별도 repo) | GPU 인스턴스 |
| llmops | 배치·GPU (별도 repo) | 배치 잡 |
| frontend | Vite 정적 빌드 (별도 repo) | CDN |

## 데이터·메시징 인프라

| 역할 | 컴포넌트 |
|---|---|
| 영구 저장 | PostgreSQL — `aria` DB + `payments` DB(별도) |
| 상태·캐시·팬아웃 | Redis — 세션·큐·토픽 상태 + pub/sub 백플레인 |
| durable 이벤트 | Kafka (FastStream) |
| 미디어 배포 | ffmpeg 합성 → 오브젝트 스토리지(HLS 세그먼트) → CDN |
| 관측성 | OTel Collector → SigNoz(시스템) · Langfuse(LLM) |

---

## 로컬 개발 (docker-compose)

`docker-compose.yml`: `postgres` · `redis` · `kafka`(KRaft 단일노드). aria/payments는 이미지 확정 후 추가. inference·llmops·SigNoz는 필요 시 개별 기동.

- 추론: 로컬은 GPU 없이 **OpenAI fallback**(`PersonaLLMPort`) 사용 → inference 컨테이너 불필요.
- 미디어: 로컬은 nginx로 HLS 서빙(운영 CDN 대체).

## 운영 예상 (AWS 기준, 미확정)

레거시가 AWS(EC2/RDS/ElastiCache/ELB)였음 → 계승 가정. **택은 미확정**.

| 계층 | 예상 |
|---|---|
| 컴퓨트 | aria/payments 컨테이너 (ECS 또는 EKS). generation/media 워커는 부하 따라 replica |
| GPU | inference = GPU 인스턴스(EC2 g-계열) 위 vLLM |
| DB | RDS PostgreSQL (aria·payments 분리) |
| 캐시/상태 | ElastiCache Redis |
| 이벤트 | MSK(관리형 Kafka) 또는 self-managed |
| 미디어 | ffmpeg 합성 → S3(HLS 세그먼트) → CloudFront(CDN) |
| 엣지 | ALB (게이트웨이 Traefik은 보류) |
| 프론트 | Vite 빌드 → S3 + CloudFront (정적) |
| 관측 | OTel Collector → SigNoz(self-host) · Langfuse(self-host) |

## 확장 전략

- **시청자 규모 → CDN이 흡수**(HLS). 앱서버와 분리 → 레거시 "100명 벽" 구조적 해소.
- **생성·미디어 부하 → 워커 replica**(generation-worker / media-worker 각각).
- **앱 stateless**(상태 Redis 외부화) → api 수평 확장 자유.
- **payments·inference는 독립 배포**라 개별 스케일/격리.

## 보안·시크릿

- 결제 시크릿·webhook은 payments 서비스에 격리(NFR-SEC-1).
- 시크릿 관리: env + 시크릿 매니저(AWS Secrets Manager 후보) — 미확정.

## 미확정

- 오케스트레이션: ECS vs EKS vs compose-on-EC2
- Kafka: MSK vs self-managed / CDN·오브젝트스토리지 선택
- IaC: Terraform 도입 여부
- 시크릿 매니저 · CI→배포 파이프라인(GitHub Actions → ?)
