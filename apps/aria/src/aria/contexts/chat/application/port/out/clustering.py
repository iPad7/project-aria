"""아웃바운드 포트: 댓글 토픽 군집화 (FR-GEN-1).

**여기가 포트인 이유**는 "어느 댓글들이 같은 얘기인가"가 모델의 문제이기 때문이다.
어휘 중첩으로도 되고 임베딩으로도 되며, 나중엔 GPU 박스(inference repo)에 얹을 수도
있다. `PersonaLLMPort`·`TtsPort`와 같은 자리다.

**반대로 "무엇이 좋은 댓글인가"는 포트 뒤로 보내지 않는다**(`domain/topic.py`).
그건 모델의 문제가 아니라 이 방송의 성격에 대한 판단이라, 순수 함수로 두어야
근거를 놓고 다툴 수 있다.

**커널이 아니라 chat의 포트다.** 구현자도 소비자도 chat이라 컨텍스트를 넘지 않는다 —
`StoryFeedPort`처럼 커널로 올릴 이유가 없다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from aria.contexts.chat.domain.topic import Candidate, Topic


class TopicClusterer(Protocol):
    def cluster(self, candidates: Sequence[Candidate]) -> list[Topic]:
        """댓글들을 토픽으로 묶는다.

        묶을 근거가 없으면 **각자 하나짜리 토픽**으로 두면 된다 — 그래도 선별은
        동작한다(질문·길이 점수는 그대로 매겨지고, 토픽 활성도만 균등해진다).
        빈 입력에는 빈 리스트.
        """
        ...
