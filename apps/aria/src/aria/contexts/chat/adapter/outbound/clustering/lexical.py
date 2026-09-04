"""어휘 중첩 기반 토픽 군집화 — 모델 없는 기본 구현.

**왜 임베딩으로 시작하지 않나.** ① 의존성이 무거워지고 CI가 hermetic하지 않게 된다
② 라이브 채팅은 짧고 어휘가 겹치는 편이라 어휘 기반이 의외로 쓸 만하다
③ 무엇보다 **이 구현이 좋은지 나쁜지 판단할 근거가 아직 없다** — 관측성(#61)으로
선별 결과를 쌓아 봐야 안다. 그전에 임베딩을 넣는 것은 측정 없이 복잡도를 사는 것이다.

임베딩이 필요해지면 같은 포트 뒤에 어댑터를 하나 더 놓으면 된다(`PersonaLLMPort`가
stub → OpenAI → 자체 vLLM 으로 간 그대로).

**단어가 아니라 문자 n-gram으로 견준다.** 한국어는 교착어라 어절 단위로는 같은 말이
안 겹친다 — 실제로 그렇게 만들었다가 실측에서 드러났다:

    "3년째 짝사랑 중인데 고백해도 될까요?"  → {3년째, 짝사랑, 중인데, 고백해도, 될까요}
    "짝사랑 고백 타이밍 언제가 좋을까요?"    → {짝사랑, 고백, 타이밍, 언제가, 좋을까요}
    교집합 {짝사랑} → 유사도 0.11 → **따로 논다**

`고백해도`·`고백`·`고백하면`이 전부 다른 토큰이기 때문이다. 형태소 분석기를 들이면
해결되지만 의존성이 무겁고, **문자 n-gram이면 접사가 붙어도 어간이 겹친다**. 짧은
채팅에서는 이 정도가 값싸고 충분하다.

**알고리즘**: 단순 탐욕적 묶기. 각 댓글을 이미 만들어진 토픽들과 견주어,
`_SIMILARITY_THRESHOLD` 이상 겹치는 첫 토픽에 넣고, 없으면 새 토픽을 연다.
O(후보 수 × 토픽 수)인데 후보가 수십 개 규모라 문제되지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from aria.contexts.chat.domain.topic import Candidate, Topic

# 이 이상 겹치면 같은 토픽으로 본다. 실측한 분포에서 관련 쌍이 0.17~0.20, 무관한
# 쌍이 0.00 이라 그 사이에 둔다. 낮추면 모든 게 한 덩어리가 되고, 높이면 따로 논다.
_SIMILARITY_THRESHOLD = 0.12

# 문자 n-gram 크기. 2면 노이즈가 많고 4면 접사 변형을 못 잡는다.
_NGRAM = 3

# 한 글자 토큰과 흔한 조사·감탄은 군집에 기여하지 않고 노이즈만 만든다.
_STOPWORDS = frozenset(
    {
        "그리고",
        "그래서",
        "하지만",
        "근데",
        "그냥",
        "너무",
        "진짜",
        "완전",
        "정말",
        "저는",
        "제가",
        "나는",
        "내가",
        "저도",
        "나도",
        "지금",
        "요즘",
    }
)

_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")


def _tokens(text: str) -> frozenset[str]:
    """의미 있는 어절을 문자 n-gram으로 펼친다.

    불용어와 한 글자 어절은 먼저 걸러 노이즈를 줄이고, 남은 것에서 n-gram을 뽑는다.
    어절이 n보다 짧으면 그 어절 자체를 쓴다.
    """
    words = [
        w for w in _TOKEN.findall(text.lower()) if len(w) > 1 and w not in _STOPWORDS
    ]
    grams: set[str] = set()
    for word in words:
        if len(word) <= _NGRAM:
            grams.add(word)
        else:
            grams.update(word[i : i + _NGRAM] for i in range(len(word) - _NGRAM + 1))
    return frozenset(grams)


def _similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """겹침 계수 — 교집합을 **작은 쪽** 크기로 나눈다.

    자카드(합집합으로 나눔)를 쓰면 문장 길이에 눌린다. 라이브 채팅은 한 줄짜리와
    긴 사연이 섞이는데, 짧은 댓글이 긴 댓글의 화제를 그대로 담고 있어도 자카드는
    낮게 나온다 — 실측에서 관련 쌍이 0.09까지 떨어졌다. "작은 쪽이 큰 쪽에 얼마나
    담겼나"가 여기서 묻고 싶은 것이다.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


class LexicalTopicClusterer:
    def cluster(self, candidates: Sequence[Candidate]) -> list[Topic]:
        # (토큰 합집합, 후보들) 로 토픽을 키워 나간다.
        buckets: list[tuple[set[str], list[Candidate]]] = []

        for candidate in candidates:
            tokens = _tokens(candidate.text)
            for bucket_tokens, members in buckets:
                if _similarity(tokens, frozenset(bucket_tokens)) >= (
                    _SIMILARITY_THRESHOLD
                ):
                    bucket_tokens |= tokens
                    members.append(candidate)
                    break
            else:
                # 묶을 데가 없으면 혼자 토픽이 된다 — 그래도 선별은 동작한다.
                buckets.append((set(tokens), [candidate]))

        return [
            Topic(label=_label(bucket_tokens, members), candidates=tuple(members))
            for bucket_tokens, members in buckets
        ]


def _label(tokens: set[str], members: list[Candidate]) -> str:
    """토픽 이름. 사람이 트레이스에서 알아볼 수 있으면 충분하다.

    가장 많이 등장한 토큰 둘을 쓰고, 없으면 첫 댓글의 앞부분으로 대신한다.
    """
    if not tokens:
        return members[0].text[:20]
    counts = {
        token: sum(1 for m in members if token in _tokens(m.text)) for token in tokens
    }
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
    return " ".join(token for token, _ in top)
