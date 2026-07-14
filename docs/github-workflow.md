# github workflow (룰북)

이 repo의 브랜칭·커밋·PR·CI·릴리스 규칙. **develop + main** 두 브랜치 모델.

## 브랜치

- **`develop`** = **기본 브랜치**. 활발한 개발의 통합 지점. 보호됨.
- **`main`** = 릴리스/운영본. 통제된 승격만 받는다. 보호됨.
- 작업 브랜치는 `develop`에서 딴다: **`<type>/<이슈번호>-<작업>-<세부>`** (kebab). 예: `feat/31-payment-create-api`, `fix/48-chat-race-condition`.
- **이슈를 먼저** 만들고 그 번호로 브랜치를 판다(트레이서빌리티). GitHub 이슈의 "Create a branch"로 만들면 자동 연결.

## 흐름

```
feature ──(squash & merge)──▶ develop ──(merge commit + tag)──▶ main
```

- **feature → develop**: PR → CI green → **squash & merge** → 원격 작업 브랜치 삭제.
- **develop → main**: 릴리스 PR → CI green → **merge commit(`--no-ff`)** → `vX.Y.Z` 태그 → GitHub Release.
  - **main으로는 squash 금지.** main과 develop이 히스토리를 공유해야 승격·백머지가 충돌 없이 된다.

## Hotfix (운영 전용 버그)

- **`main`에서** `fix/...` 브랜치 → PR **to main** → merge → patch 태그(`vX.Y.Z+1`).
- 그다음 **`main`을 `develop`으로 백머지**(develop 최신 유지). develop↔main이 real merge라 충돌 없음.

## 커밋

**`<type>: <세부>`** — 스코프 없음. 영역은 브랜치(이슈번호)가 이미 나타낸다. 커밋은 작은 단위로 쌓는다.

- **type**: feat · fix · docs · refactor · test · chore · ci · build · perf
- 예 (한 브랜치에서 누적): `feat: 서비스 로직 구현` → `fix: 동시성 이슈 처리` → `feat: 컨트롤러 구현`
- 파괴적 변경은 `!` 또는 `BREAKING CHANGE:`.

> **squash 주의**: feature→develop이 squash라 위 커밋들은 develop에서 **한 커밋으로 합쳐진다**. 따라서 develop 히스토리에 남는 건 **PR 제목**이다 → PR 제목도 `<type>: <요약>`으로 쓰고, 본문에 `Closes #<이슈>`를 넣어 이슈를 자동 종료·연결한다.

## PR

- 템플릿(`.github/pull_request_template.md`) 채우기. **작게** 유지.
- **머지 조건: CI green.** 1인이라 승인 0 셀프머지 OK(단 PR·CI 필수).
- 머지 방식: feature→develop = **squash**, develop→main·hotfix = **merge commit**.
- 머지 후 원격 브랜치 삭제(자동).

## CI 게이트 (`.github/workflows/ci.yml`)

`main`·`develop` push와 모든 PR에 실행, 전부 통과해야 머지:

1. `ruff check` · `ruff format --check`
2. `lint-imports` — aria·payments 경계
3. `pytest`
4. `uv build --all-packages`

## 릴리스 — main 기준, SemVer

- develop→main 머지 = 릴리스. `main`에 태그 `vMAJOR.MINOR.PATCH`(1.0 전 `0.x`) → GitHub Release.

## 라벨 (권장)

- type: `feat` `fix` `docs` `refactor` `chore` `ci`
- context: `identity` `persona` `chat` `streaming` `wallet` `payments` `common` `infra`
- 기타: `priority:high` `blocked` `good first issue`

## 이슈

- 버그·기능 템플릿(`.github/ISSUE_TEMPLATE/`). 빈 이슈 비활성.
