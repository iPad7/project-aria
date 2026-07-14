# github workflow (룰북)

이 repo의 브랜칭·커밋·PR·CI·릴리스 규칙. **develop + main** 두 브랜치 모델.

## 브랜치

- **`develop`** = **기본 브랜치**. 활발한 개발의 통합 지점. 보호됨.
- **`main`** = 릴리스/운영본. 통제된 승격만 받는다. 보호됨.
- 작업 브랜치는 `develop`에서 딴다: `<type>/<scope>-<요약>` (kebab). 예: `feat/chat-ws-consumer`, `fix/payments-webhook-idempotency`.

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

## 커밋 — Conventional Commits

`<type>(<scope>): <요약>`

- **type**: feat · fix · docs · refactor · test · chore · ci · build · perf
- **scope**: identity · persona · chat · streaming · wallet · payments · common · inference · infra · docs · ci
- 예: `feat(chat): websocket consumer + redis fanout` · `fix(payments): make Toss webhook idempotent`
- 본문에 **왜**. 파괴적 변경은 `!` 또는 `BREAKING CHANGE:`.

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
