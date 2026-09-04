"""토픽과 댓글 선별 (FR-GEN-1·2).

**지금까지의 "선별"은 Redis 락 경쟁이었다.** 메시지마다 생성 요청이 나가고, 슬롯을
못 잡은 것은 조용히 버려졌다 — 즉 *동시에 들어온 것 중 먼저 잡은 것*이 답했다.
초당 수십 개가 들어오는 방송에서는 아무 의미가 없다. 여기가 그것을 대체한다.

**점수 규칙을 도메인에 둔다.** 임베딩이나 모델 없이 순수 함수로 테스트할 수 있어야,
"왜 저 댓글을 골랐나"를 근거로 다툴 수 있다. 군집화(어느 댓글들이 같은 얘기인가)만
포트 뒤에 두고, **무엇이 좋은 댓글인가는 여기서 결정한다** — 그건 모델의 문제가
아니라 이 방송의 성격에 대한 판단이기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field

# 물음표로 끝나거나 의문사를 담은 댓글은 반응할 거리가 분명하다.
_QUESTION_MARKS = ("?", "？")
_QUESTION_WORDS = (
    "어떻게",
    "어떡",
    "왜",
    "뭐",
    "무엇",
    "언제",
    "누구",
    "어디",
    "할까",
    "될까",
    "괜찮",
    "맞나",
    "맞아",
    "인가요",
    "나요",
)

# 너무 짧으면 반응할 내용이 없고("ㅋㅋ", "ㅇㅇ"), 너무 길면 낭독이 지루하다.
_MIN_USEFUL_LEN = 6
_IDEAL_LEN = 40


class Candidate(BaseModel):
    """응답 후보가 되는 시청자 댓글."""

    message_id: UUID
    author_id: UUID
    text: str = Field(min_length=1, max_length=500)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class Topic:
    """같은 얘기를 하는 댓글 묶음.

    군집화는 포트가 하고(어휘 기반이든 임베딩이든), 여기는 그 결과를 담기만 한다.
    """

    label: str
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)

    @property
    def size(self) -> int:
        """이 토픽이 얼마나 활발한가 — 활성 토픽 판단의 근거(FR-GEN-1)."""
        return len(self.candidates)


@dataclass(frozen=True)
class Scored:
    candidate: Candidate
    score: float
    reasons: tuple[str, ...]


def _length_score(text: str) -> float:
    """길이 점수. 짧으면 0에 가깝고 이상적 길이에서 1, 그 뒤로 완만히 준다."""
    n = len(text.strip())
    if n < _MIN_USEFUL_LEN:
        return 0.0
    if n <= _IDEAL_LEN:
        return n / _IDEAL_LEN
    # 길어도 0.5 아래로는 안 떨어뜨린다 — 긴 사연형 댓글도 답할 가치가 있다.
    return max(0.5, _IDEAL_LEN / n)


def _is_question(text: str) -> bool:
    stripped = text.strip()
    if stripped.endswith(_QUESTION_MARKS):
        return True
    return any(word in stripped for word in _QUESTION_WORDS)


def score(candidate: Candidate, *, topic_size: int, max_topic_size: int) -> Scored:
    """후보 하나의 점수와 **그 이유**를 낸다.

    이유를 함께 내는 것이 중요하다 — 트레이스에 실어야 "왜 저걸 골랐나"에 답할 수
    있고, 점수 규칙을 고칠 때 무엇이 달라졌는지 보인다.

    세 가지를 본다:

    - **질문인가** — 반응할 거리가 분명하다. 스트리머가 가장 답하기 좋은 형태다.
    - **길이** — 너무 짧으면 답할 내용이 없고("ㅋㅋ"), 너무 길면 낭독이 지루하다.
    - **토픽 활성도** — 여럿이 같은 얘기를 하고 있으면 그게 지금 방송의 화제다
      (FR-GEN-1의 "활성 토픽"). 한 명만 하는 얘기보다 답할 값이 크다.

    최신성은 **점수에 넣지 않는다.** 후보 버퍼가 이미 최근 것만 담고(TTL·상한),
    거기에 시간 가중까지 주면 사실상 "가장 최근 것"만 뽑혀 선별이 무의미해진다.
    """
    reasons: list[str] = []

    question = 1.0 if _is_question(candidate.text) else 0.0
    if question:
        reasons.append("question")

    length = _length_score(candidate.text)
    if length >= 0.8:
        reasons.append("well-sized")
    elif length == 0.0:
        reasons.append("too-short")

    # 가장 큰 토픽을 1로 두는 상대 점수. 절대 개수는 방송 규모마다 달라 의미가 없다.
    activity = topic_size / max_topic_size if max_topic_size else 0.0
    if activity >= 0.8 and topic_size > 1:
        reasons.append("hot-topic")

    total = 0.5 * question + 0.3 * length + 0.2 * activity
    return Scored(candidate=candidate, score=round(total, 4), reasons=tuple(reasons))


def select_best(topics: list[Topic]) -> Scored | None:
    """토픽들에서 답할 댓글 하나를 고른다. 후보가 없으면 None.

    **하나만 고른다.** 한 방에서 동시에 하나의 응답만 만들기 때문이고(단일 플라이트),
    나머지는 버린다 — 나중에 답하려고 쌓아 두면 30초 전 댓글에 뒤늦게 답하게 된다.

    동점은 **먼저 온 댓글**이 이긴다. 기준이 점수뿐이면 같은 점수끼리 순서가 실행마다
    달라져 재현이 안 된다(열혈순위의 동점 처리와 같은 이유).
    """
    max_size = max((t.size for t in topics), default=0)
    scored = [
        score(candidate, topic_size=topic.size, max_topic_size=max_size)
        for topic in topics
        for candidate in topic.candidates
    ]
    if not scored:
        return None
    return max(scored, key=lambda s: (s.score, -s.candidate.created_at.timestamp()))
