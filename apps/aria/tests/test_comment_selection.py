"""댓글 선별 — 토픽 군집화·베스트 댓글 (FR-GEN-1·2).

이 단위의 위험은 넷이다:

1. **점수 규칙이 설명 불가능해지는 것.** "왜 저걸 골랐나"에 답할 수 있어야 한다.
2. **비결정성.** 같은 후보에 같은 답이 나와야 재현·비교가 된다.
3. **군집이 무의미해지는 것.** 전부 한 덩어리이거나 전부 따로면 활성 토픽이 없다.
4. **후보가 무한정 쌓이는 것.** 오래된 댓글에 뒤늦게 답하는 것이 더 이상하다.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from aria.contexts.chat.adapter.outbound.clustering.lexical import (
    LexicalTopicClusterer,
)
from aria.contexts.chat.adapter.outbound.redis.candidates import (
    RedisCandidateBuffer,
)
from aria.contexts.chat.domain.topic import Candidate, Topic, score, select_best


def _c(text: str, *, at: datetime | None = None) -> Candidate:
    return Candidate(
        message_id=uuid4(),
        author_id=uuid4(),
        text=text,
        created_at=at or datetime.now(UTC),
    )


# --- 점수 규칙 (모델 없이 순수 함수) -----------------------------------------


def test_question_outranks_a_statement() -> None:
    # 질문은 반응할 거리가 분명하다 — 스트리머가 가장 답하기 좋은 형태다.
    question = score(_c("헤어지는 게 맞을까요?"), topic_size=1, max_topic_size=1)
    statement = score(_c("오늘 날씨가 참 좋습니다"), topic_size=1, max_topic_size=1)

    assert question.score > statement.score
    assert "question" in question.reasons


def test_question_words_count_without_a_question_mark() -> None:
    # 라이브 채팅은 물음표를 자주 생략한다.
    scored = score(_c("이럴 땐 어떻게 해야 하나"), topic_size=1, max_topic_size=1)

    assert "question" in scored.reasons


def test_too_short_comments_score_zero_on_length() -> None:
    # "ㅋㅋ", "ㅇㅇ" 에는 답할 내용이 없다.
    scored = score(_c("ㅋㅋ"), topic_size=1, max_topic_size=1)

    assert "too-short" in scored.reasons


def test_very_long_comments_are_not_crushed() -> None:
    # 긴 사연형 댓글도 답할 가치가 있다 — 길이 점수를 0.5 아래로 떨어뜨리지 않는다.
    long_text = "제가 3년 동안 짝사랑을 하고 있는데 " * 8
    short_ok = score(_c("고백해도 될까요"), topic_size=1, max_topic_size=1)
    long_one = score(_c(long_text[:400]), topic_size=1, max_topic_size=1)

    assert long_one.score > 0
    assert short_ok.score > 0


def test_hot_topic_beats_a_lone_voice() -> None:
    """여럿이 같은 얘기를 하면 그게 지금 방송의 화제다(FR-GEN-1의 '활성 토픽')."""
    text = "고백하는 게 맞을까요"
    hot = score(_c(text), topic_size=5, max_topic_size=5)
    lonely = score(_c(text), topic_size=1, max_topic_size=5)

    assert hot.score > lonely.score
    assert "hot-topic" in hot.reasons


def test_reasons_explain_the_score() -> None:
    # 트레이스에 실어야 "왜 저걸 골랐나"에 답할 수 있다.
    scored = score(_c("헤어지는 게 맞을까요?"), topic_size=4, max_topic_size=4)

    assert set(scored.reasons) >= {"question", "hot-topic"}


def test_recency_is_not_part_of_the_score() -> None:
    """최신성을 넣으면 사실상 '가장 최근 것'만 뽑혀 선별이 무의미해진다."""
    now = datetime.now(UTC)
    old = score(
        _c("고백할까요?", at=now - timedelta(minutes=4)), topic_size=1, max_topic_size=1
    )
    new = score(_c("고백할까요?", at=now), topic_size=1, max_topic_size=1)

    assert old.score == new.score


# --- 고르기 ------------------------------------------------------------------


def test_no_candidates_selects_nothing() -> None:
    assert select_best([]) is None
    assert select_best([Topic(label="빈 토픽")]) is None


def test_selects_exactly_one() -> None:
    # 한 방에서 동시에 하나의 응답만 만든다(단일 플라이트).
    topic = Topic(
        label="고백",
        candidates=(_c("고백할까요?"), _c("ㅋㅋ"), _c("헤어질까요?")),
    )

    best = select_best([topic])

    assert best is not None
    assert best.candidate.text in {"고백할까요?", "헤어질까요?"}


def test_ties_go_to_the_earlier_comment() -> None:
    # 기준이 점수뿐이면 같은 점수끼리 순서가 실행마다 달라져 재현이 안 된다.
    now = datetime.now(UTC)
    early = _c("고백할까요?", at=now - timedelta(seconds=30))
    late = _c("고백할까요?", at=now)

    best = select_best([Topic(label="고백", candidates=(late, early))])

    assert best is not None and best.candidate.message_id == early.message_id


def test_selection_is_deterministic() -> None:
    topic = Topic(
        label="고백",
        candidates=tuple(
            _c(t) for t in ("ㅇㅇ", "고백할까요?", "날씨 좋네요", "ㅋㅋㅋ")
        ),
    )

    picks = {select_best([topic]).candidate.message_id for _ in range(5)}

    assert len(picks) == 1


def test_active_topic_wins_over_an_equally_good_lone_comment() -> None:
    # 같은 문장이라도 여럿이 말하는 쪽을 고른다.
    text = "고백하는 게 맞을까요"
    hot = Topic(label="고백", candidates=tuple(_c(text) for _ in range(4)))
    lonely = Topic(label="날씨", candidates=(_c(text),))

    best = select_best([lonely, hot])

    assert best is not None
    assert "hot-topic" in best.reasons


# --- 군집화 (어휘 기반) ------------------------------------------------------


def test_similar_comments_land_in_one_topic() -> None:
    clusterer = LexicalTopicClusterer()

    topics = clusterer.cluster(
        [
            _c("짝사랑 고백 어떻게 하나요"),
            _c("짝사랑 고백 타이밍이 언제인가요"),
        ]
    )

    assert len(topics) == 1
    assert topics[0].size == 2


def test_unrelated_comments_split_into_topics() -> None:
    clusterer = LexicalTopicClusterer()

    topics = clusterer.cluster(
        [_c("짝사랑 고백 어떻게 하나요"), _c("점심 메뉴 추천 부탁해요")]
    )

    assert len(topics) == 2


def test_clustering_is_empty_for_no_candidates() -> None:
    assert LexicalTopicClusterer().cluster([]) == []


def test_every_candidate_ends_up_in_exactly_one_topic() -> None:
    # 후보를 잃어버리거나 중복해서 담으면 활성도 계산이 어긋난다.
    candidates = [
        _c("짝사랑 고백 어떻게"),
        _c("점심 메뉴 추천"),
        _c("짝사랑 고백 타이밍"),
        _c("주말에 뭐 하세요"),
    ]

    topics = LexicalTopicClusterer().cluster(candidates)

    placed = [c.message_id for t in topics for c in t.candidates]
    assert sorted(map(str, placed)) == sorted(str(c.message_id) for c in candidates)


def test_topic_label_is_recognisable() -> None:
    # 트레이스에서 사람이 알아볼 수 있으면 충분하다.
    topics = LexicalTopicClusterer().cluster(
        [_c("짝사랑 고백 어떻게"), _c("짝사랑 고백 타이밍")]
    )

    assert "짝사랑" in topics[0].label or "고백" in topics[0].label


# --- 후보 버퍼 ---------------------------------------------------------------


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis(server=FakeServer(), decode_responses=True)


async def test_buffer_returns_candidates_in_arrival_order(
    redis: FakeAsyncRedis,
) -> None:
    buffer = RedisCandidateBuffer(redis)
    room = uuid4()
    for text in ("첫째", "둘째", "셋째"):
        await buffer.add(room, _c(text))

    assert [c.text for c in await buffer.take_all(room)] == ["첫째", "둘째", "셋째"]


async def test_take_all_consumes(redis: FakeAsyncRedis) -> None:
    """읽기가 아니라 소비다 — 비우지 않으면 다음 틱에 같은 후보를 또 저울질한다."""
    buffer = RedisCandidateBuffer(redis)
    room = uuid4()
    await buffer.add(room, _c("안녕하세요"))

    assert len(await buffer.take_all(room)) == 1
    assert await buffer.take_all(room) == []


async def test_buffer_keeps_only_the_recent_ones(redis: FakeAsyncRedis) -> None:
    # 오래된 채팅은 후보로서 가치가 없다. 상한을 넘으면 오래된 것부터 밀려난다.
    buffer = RedisCandidateBuffer(redis)
    room = uuid4()
    for i in range(60):
        await buffer.add(room, _c(f"메시지 {i}"))

    kept = await buffer.take_all(room)

    assert len(kept) == 50
    assert kept[0].text == "메시지 10"  # 앞의 10개가 밀려났다
    assert kept[-1].text == "메시지 59"


async def test_buffer_expires(redis: FakeAsyncRedis) -> None:
    # TTL이 없으면 끝난 방송의 잔재가 남는다.
    buffer = RedisCandidateBuffer(redis)
    room = uuid4()
    await buffer.add(room, _c("안녕하세요"))

    assert await redis.ttl(f"chat:candidates:{room}") > 0


async def test_rooms_do_not_share_candidates(redis: FakeAsyncRedis) -> None:
    buffer = RedisCandidateBuffer(redis)
    mine, other = uuid4(), uuid4()
    await buffer.add(mine, _c("내 방 메시지"))
    await buffer.add(other, _c("남의 방 메시지"))

    assert [c.text for c in await buffer.take_all(mine)] == ["내 방 메시지"]


async def test_corrupt_entries_are_skipped(redis: FakeAsyncRedis) -> None:
    # 형식이 바뀐 잔재 하나 때문에 진행이 멈추면 안 된다.
    buffer = RedisCandidateBuffer(redis)
    room = uuid4()
    await buffer.add(room, _c("멀쩡한 메시지"))
    await redis.rpush(f"chat:candidates:{room}", "이건 JSON이 아니다")

    assert [c.text for c in await buffer.take_all(room)] == ["멀쩡한 메시지"]


def test_korean_inflections_cluster_together() -> None:
    """한국어는 교착어라 어절 단위로는 같은 말이 안 겹친다 — 실측에서 드러났다.

    `고백해도`·`고백`·`고백하면` 은 전부 다른 토큰이라, 어절로 견주면 "짝사랑 고백"
    얘기 셋이 따로 놀았다(토픽 6개). 문자 n-gram 으로 어간이 겹치게 한다.
    """
    topics = LexicalTopicClusterer().cluster(
        [
            _c("3년째 짝사랑 중인데 고백해도 될까요?"),
            _c("짝사랑 고백 타이밍 언제가 좋을까요?"),
            _c("짝사랑 고백하면 친구도 못 되나요?"),
        ]
    )

    assert len(topics) == 1
    assert topics[0].size == 3


def test_chatter_does_not_get_swallowed_into_the_hot_topic() -> None:
    # 임계값이 낮으면 모든 게 한 덩어리가 되어 활성 토픽이 의미를 잃는다.
    topics = LexicalTopicClusterer().cluster(
        [
            _c("3년째 짝사랑 중인데 고백해도 될까요?"),
            _c("짝사랑 고백 타이밍 언제가 좋을까요?"),
            _c("오늘 날씨 좋네요"),
            _c("점심 메뉴 추천 부탁해요"),
        ]
    )

    sizes = sorted(t.size for t in topics)
    assert sizes == [1, 1, 2]  # 고백 묶음 하나 + 잡담 둘
