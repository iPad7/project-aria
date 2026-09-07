"""방송 수명주기 스모크 — 통합 테스트를 실제 인프라에 대고 돌린다.

검증 내용은 `apps/aria/tests/test_lifecycle_integration.py`에 있고 여기는 그것을 부르는
얇은 러너다. **한 벌만 존재해야** CI가 보는 것과 손으로 돌려 본 것이 갈리지 않는다 —
스크립트가 자기 검증을 따로 들고 있으면 둘이 조용히 어긋난다.

    docker compose up -d
    cd apps/aria && uv run alembic upgrade head
    uv run python scripts/smoke_lifecycle.py

종료 코드 0이면 통과. pytest로 직접 부르는 것과 같다: `uv run pytest -m integration`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# cwd와 무관하게 같은 파일을 가리킨다. pytest는 이 경로에서 위로 올라가 repo 루트의
# pyproject.toml을 rootdir로 잡으므로 `pythonpath` 같은 설정도 그대로 적용된다.
TARGET = (
    Path(__file__).resolve().parent.parent
    / "apps/aria/tests/test_lifecycle_integration.py"
)


def main() -> int:
    # 기본 설정이 `-m 'not integration'`으로 빼 두는 것을 뒤의 `-m`이 되돌린다.
    return int(pytest.main(["-m", "integration", "-v", str(TARGET)]))


if __name__ == "__main__":
    sys.exit(main())
