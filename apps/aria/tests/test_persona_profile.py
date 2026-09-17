"""페르소나 해석 — 말투·나침반·가치관이 응답에 반영되는가.

이 단위의 위험은 넷이다:

1. **프롬프트 합성** — 우선순위가 사라지거나, 숫자 척도가 뜻 없이 들어가거나,
   나침반이 가치 목록에 섞여 둘 다 흐려지는 것.
2. **폴백** — 프로필 없는 기존 페르소나가 생성에서 죽는 것.
3. **경계** — 어댑터가 페르소나를 조회하게 되어 앱/추론 경계가 무너지는 것.
4. **교체 의미론** — 가치관 목록이 부분 수정처럼 동작해 우선순위가 밀리는 것.
"""

import json
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeRedis, FakeServer
from room_harness import memory_session_override
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.testclient import TestClient

from aria.app import create_app
from aria.common.config import settings
from aria.common.db import get_session
from aria.common.persona_profile import PersonaProfile
from aria.common.redis import get_redis, get_sync_redis
from aria.contexts.chat.application.persona_prompt import (
    DEFAULT_SYSTEM,
    system_message,
)
from aria.contexts.identity.adapter.outbound.security.jwt_token_service import (
    JwtTokenService,
)
from aria.contexts.persona.adapter.outbound.cache.profile import (
    CachedPersonaProfiles,
    invalidate,
)
from aria.contexts.persona.adapter.outbound.persistence.repository import (
    SqlModelPersonaRepository,
    SqlModelProfileRepository,
)
from aria.contexts.persona.adapter.outbound.profile import PersonaProfileProvider
from aria.contexts.persona.domain.model import (
    CommunicationStyle,
    MoralCompass,
    Persona,
)

# --- 프롬프트 합성 (이 단위의 핵심) -----------------------------------------


def _profile(**kwargs) -> PersonaProfile:
    base = {"persona_id": uuid4(), "name": "아리아"}
    return PersonaProfile(**(base | kwargs))


def test_profileless_persona_falls_back_to_the_common_prompt() -> None:
    # 기존 페르소나에는 말투가 없다. 여기서 거부하면 전부 죽는다.
    assert system_message(None).content == DEFAULT_SYSTEM
    assert system_message(_profile()).content == DEFAULT_SYSTEM


def test_missing_profile_and_empty_profile_are_treated_alike() -> None:
    # 잘못된 persona_id와 "아직 말투를 안 정한 정상 페르소나"는 원인이 다르지만,
    # 방송을 멈출 이유가 아니라는 점에서는 같다.
    assert system_message(None).content == system_message(_profile()).content


def test_tone_makes_the_prompt_persona_specific() -> None:
    message = system_message(_profile(tone="장난기 있고 솔직한"))

    assert message.role == "system"
    assert message.content != DEFAULT_SYSTEM
    assert "아리아" in message.content
    assert "장난기 있고 솔직한" in message.content


def test_directness_becomes_a_sentence_not_a_number() -> None:
    """숫자를 그대로 넣으면 모델이 그 척도가 무엇인지 모른다 — "3"은 아무 뜻이 없다."""
    blunt = system_message(_profile(tone="솔직한", directness=5)).content
    gentle = system_message(_profile(tone="솔직한", directness=1)).content

    assert "5" not in blunt
    assert blunt != gentle
    assert "직설적" in blunt


def test_core_values_keep_their_priority() -> None:
    # 순서가 곧 우선순위다. 집합처럼 다루면 그 정보가 사라진다.
    content = system_message(
        _profile(tone="따뜻한", core_values=("정직", "성장", "안정"))
    ).content

    assert (
        content.index("1. 정직") < content.index("2. 성장") < content.index("3. 안정")
    )
    # 충돌 시 어느 쪽을 따를지까지 일러 줘야 우선순위가 쓰인다.
    assert "부딪히면" in content


def test_core_values_alone_are_enough_to_have_a_voice() -> None:
    # 말투가 없어도 가치관이 있으면 그 페르소나답게 말할 재료가 있다.
    assert system_message(_profile(core_values=("정직",))).content != DEFAULT_SYSTEM


def test_empty_optional_fields_do_not_leave_dangling_labels() -> None:
    content = system_message(_profile(tone="차분한")).content

    for label in ("문장 길이:", "공감 표현:", "질문 방식:"):
        assert label not in content


# --- 도덕 나침반 -------------------------------------------------------------


def test_the_compass_is_its_own_paragraph_not_another_value() -> None:
    """가치관은 "무엇을 중시하는가", 나침반은 "부딪혔을 때 어느 쪽"이다.

    한 덩어리로 주면 모델이 나침반을 가치 목록의 연장으로 읽어 둘 다 흐려진다.
    """
    content = system_message(
        _profile(
            tone="따뜻한",
            core_values=("정직", "성장"),
            moral_standard="관계가 회복될 수 있는지를 먼저 본다",
        )
    ).content

    # 나침반 문단이 가치 목록 **뒤에** 오고, 자기 머리말을 갖는다.
    assert content.index("중시하는 가치") < content.index(
        "판단이 필요할 때 따르는 기준"
    )
    assert "관계가 회복될 수 있는지를 먼저 본다" in content


def test_a_compass_alone_is_enough_to_have_a_voice() -> None:
    # 말투가 없어도 "무엇을 근거로 판단하는가"는 응답을 그 페르소나답게 만든다.
    profile = _profile(moral_standard="약속을 지켰는지부터 본다")

    assert profile.has_voice() is True
    assert system_message(profile).content != DEFAULT_SYSTEM


def test_the_standard_anchors_the_compass() -> None:
    """판단 기준 없이 나머지 축만 있으면 문단의 머리말이 가리킬 것이 없다."""
    profile = _profile(tone="따뜻한", rule_adherence="사정을 먼저 듣는다")

    assert profile.has_compass() is False
    assert "판단이 필요할 때" not in system_message(profile).content


def test_optional_compass_axes_do_not_leave_dangling_labels() -> None:
    content = system_message(_profile(moral_standard="사실관계부터 본다")).content

    assert "원칙과 사정이 부딪히면" not in content
    assert "공정하다는 것은" not in content


def test_two_compasses_produce_different_prompts() -> None:
    # 같은 말투라도 나침반이 다르면 같은 사연에 다르게 답해야 한다.
    strict = system_message(
        _profile(tone="차분한", moral_standard="약속을 지켰는지부터 본다")
    ).content
    lenient = system_message(
        _profile(tone="차분한", moral_standard="어떤 사정이 있었는지부터 본다")
    ).content

    assert strict != lenient


# --- 영속성 -----------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def persona(session: Session) -> Persona:
    p = Persona(owner_id=uuid4(), name="아리아", description="연애상담 스트리머")
    SqlModelPersonaRepository(session).add(p)
    return p


def test_style_is_upserted_not_duplicated(session: Session, persona: Persona) -> None:
    # 1:1이라 두 번 설정해도 행이 하나여야 한다.
    repo = SqlModelProfileRepository(session)
    repo.set_style(CommunicationStyle(persona_id=persona.id, tone="따뜻한"))
    repo.set_style(CommunicationStyle(persona_id=persona.id, tone="차가운"))

    style = repo.get_style(persona.id)
    assert style is not None and style.tone == "차가운"


def test_compass_is_upserted_not_duplicated(session: Session, persona: Persona) -> None:
    repo = SqlModelProfileRepository(session)
    repo.set_compass(MoralCompass(persona_id=persona.id, standard="사실관계부터 본다"))
    repo.set_compass(MoralCompass(persona_id=persona.id, standard="사정부터 듣는다"))

    compass = repo.get_compass(persona.id)
    assert compass is not None and compass.standard == "사정부터 듣는다"


def test_style_and_compass_are_independent(session: Session, persona: Persona) -> None:
    # 나침반만 정한 페르소나가 정상이다 — 한쪽 설정이 다른 쪽을 만들지 않는다.
    repo = SqlModelProfileRepository(session)
    repo.set_compass(MoralCompass(persona_id=persona.id, standard="사실관계부터 본다"))

    assert repo.get_style(persona.id) is None
    assert repo.get_compass(persona.id) is not None


def test_core_values_are_replaced_wholesale(session: Session, persona: Persona) -> None:
    """부분 수정이 아니라 교체다 — 하나만 빼면 나머지 순위가 전부 밀린다."""
    repo = SqlModelProfileRepository(session)
    repo.set_core_values(persona.id, ["정직", "성장", "안정"])
    repo.set_core_values(persona.id, ["안정", "정직"])

    assert repo.list_core_values(persona.id) == ["안정", "정직"]


def test_core_value_vocabulary_is_shared(session: Session) -> None:
    # 같은 단어를 두 페르소나가 쓰면 어휘 행은 하나여야 한다.
    repo = SqlModelProfileRepository(session)

    first = repo.ensure_value("정직")
    second = repo.ensure_value("정직")

    assert first.id == second.id


def test_core_values_survive_a_reorder(session: Session, persona: Persona) -> None:
    # 같은 항목을 순서만 바꿔 저장 — 유일 제약(persona, priority)에 걸리면 안 된다.
    repo = SqlModelProfileRepository(session)
    repo.set_core_values(persona.id, ["정직", "성장"])
    repo.set_core_values(persona.id, ["성장", "정직"])

    assert repo.list_core_values(persona.id) == ["성장", "정직"]


# --- 포트 어댑터 -------------------------------------------------------------


async def test_provider_returns_none_for_an_unknown_persona(
    session: Session,
) -> None:
    # 없음(None)과 비어 있음은 다르다 — 앞은 잘못된 id다.
    provider = PersonaProfileProvider(
        SqlModelPersonaRepository(session), SqlModelProfileRepository(session)
    )

    assert await provider.profile_of(uuid4()) is None


async def test_provider_builds_a_voiceless_profile_for_a_bare_persona(
    session: Session, persona: Persona
) -> None:
    provider = PersonaProfileProvider(
        SqlModelPersonaRepository(session), SqlModelProfileRepository(session)
    )

    profile = await provider.profile_of(persona.id)

    assert profile is not None
    assert profile.name == "아리아"
    assert profile.has_voice() is False  # 말투도 가치관도 없다


async def test_provider_carries_style_and_values(
    session: Session, persona: Persona
) -> None:
    profiles = SqlModelProfileRepository(session)
    profiles.set_style(
        CommunicationStyle(persona_id=persona.id, tone="따뜻한", directness=4)
    )
    profiles.set_core_values(persona.id, ["정직", "성장"])

    profile = await PersonaProfileProvider(
        SqlModelPersonaRepository(session), profiles
    ).profile_of(persona.id)

    assert profile is not None
    assert profile.tone == "따뜻한"
    assert profile.directness == 4
    assert profile.core_values == ("정직", "성장")
    assert profile.has_voice() is True


async def test_provider_carries_the_compass(session: Session, persona: Persona) -> None:
    profiles = SqlModelProfileRepository(session)
    profiles.set_compass(
        MoralCompass(
            persona_id=persona.id,
            standard="관계가 회복될 수 있는지를 먼저 본다",
            fairness="잘잘못을 가리기보다 각자의 몫을 나눈다",
        )
    )

    profile = await PersonaProfileProvider(
        SqlModelPersonaRepository(session), profiles
    ).profile_of(persona.id)

    assert profile is not None
    assert profile.moral_standard == "관계가 회복될 수 있는지를 먼저 본다"
    assert profile.fairness == "잘잘못을 가리기보다 각자의 몫을 나눈다"
    assert profile.has_compass() is True


# --- 캐시 --------------------------------------------------------------------


class _CountingProvider:
    def __init__(self, profile: PersonaProfile | None) -> None:
        self._profile = profile
        self.hits = 0

    async def profile_of(self, persona_id: UUID) -> PersonaProfile | None:
        self.hits += 1
        return self._profile


async def test_second_read_is_served_from_cache() -> None:
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    profile = _profile(tone="따뜻한", core_values=("정직",))
    inner = _CountingProvider(profile)
    cached = CachedPersonaProfiles(inner, redis)

    first = await cached.profile_of(profile.persona_id)
    second = await cached.profile_of(profile.persona_id)

    assert inner.hits == 1
    assert first == second  # 왕복해도 값이 그대로다


async def test_invalidation_makes_the_next_read_go_to_the_source() -> None:
    """열혈순위 캐시와 다른 점 — 여기는 쓰기도 이 컨텍스트의 것이라 훅을 걸 수 있다."""
    server = FakeServer()
    redis = FakeAsyncRedis(server=server, decode_responses=True)
    sync_redis = FakeRedis(server=server, decode_responses=True)
    profile = _profile(tone="따뜻한")
    inner = _CountingProvider(profile)
    cached = CachedPersonaProfiles(inner, redis)

    await cached.profile_of(profile.persona_id)
    invalidate(sync_redis, profile.persona_id)
    await cached.profile_of(profile.persona_id)

    assert inner.hits == 2


async def test_cache_round_trip_keeps_the_compass() -> None:
    # 직렬화가 필드를 빠뜨리면 캐시 히트에서만 나침반이 사라진다 — 재현이 어려운
    # 종류의 버그라 왕복을 못 박는다.
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    profile = _profile(
        tone="따뜻한",
        moral_standard="관계가 회복될 수 있는지를 먼저 본다",
        rule_adherence="약속은 지켜야 하지만 사정을 먼저 듣는다",
        fairness="각자의 몫을 나눈다",
    )
    cached = CachedPersonaProfiles(_CountingProvider(profile), redis)

    await cached.profile_of(profile.persona_id)
    second = await cached.profile_of(profile.persona_id)

    assert second == profile


async def test_a_cache_entry_from_before_the_compass_is_refetched() -> None:
    """필드가 늘어난 배포 직후 옛 캐시가 남아 있다 — 손상된 값으로 보고 스스로 낫는다."""
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    profile = _profile(tone="따뜻한", moral_standard="사실관계부터 본다")
    inner = _CountingProvider(profile)
    cached = CachedPersonaProfiles(inner, redis)
    await redis.set(
        f"persona:profile:{profile.persona_id}",
        json.dumps({"persona_id": str(profile.persona_id), "name": "아리아"}),
    )

    assert await cached.profile_of(profile.persona_id) == profile
    assert inner.hits == 1


async def test_unknown_persona_is_not_cached() -> None:
    # 없는 id를 캐시하면 새로 만든 페르소나가 TTL만큼 안 보인다.
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    inner = _CountingProvider(None)
    cached = CachedPersonaProfiles(inner, redis)

    await cached.profile_of(uuid4())
    await cached.profile_of(uuid4())

    assert inner.hits == 2


# --- HTTP --------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    server = FakeServer()
    app = create_app()
    app.dependency_overrides[get_session] = memory_session_override()
    app.dependency_overrides[get_sync_redis] = lambda: FakeRedis(
        server=server, decode_responses=True
    )
    app.dependency_overrides[get_redis] = lambda: FakeAsyncRedis(
        server=server, decode_responses=True
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _headers(user_id: UUID) -> dict[str, str]:
    tokens = JwtTokenService(settings.jwt_secret, settings.jwt_algorithm, 3600)
    return {"Authorization": f"Bearer {tokens.issue_access_token(user_id)}"}


def _own_persona(client: TestClient, owner: UUID) -> str:
    res = client.post("/personas", headers=_headers(owner), json={"name": "아리아"})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_setting_style_requires_ownership(client: TestClient) -> None:
    persona_id = _own_persona(client, uuid4())

    res = client.put(
        f"/personas/{persona_id}/style",
        headers=_headers(uuid4()),  # 남의 페르소나
        json={"tone": "내 맘대로"},
    )

    assert res.status_code == 403


def test_owner_sets_style_and_values(client: TestClient) -> None:
    owner = uuid4()
    persona_id = _own_persona(client, owner)

    client.put(
        f"/personas/{persona_id}/style",
        headers=_headers(owner),
        json={"tone": "따뜻하고 나긋한", "directness": 2},
    )
    client.put(
        f"/personas/{persona_id}/core-values",
        headers=_headers(owner),
        json={"values": ["정직", "성장"]},
    )

    voice = client.get(f"/personas/{persona_id}/voice").json()  # 공개 조회
    assert voice["style"]["tone"] == "따뜻하고 나긋한"
    assert voice["style"]["directness"] == 2
    assert voice["core_values"] == ["정직", "성장"]


def test_bare_persona_has_no_style_yet(client: TestClient) -> None:
    persona_id = _own_persona(client, uuid4())

    voice = client.get(f"/personas/{persona_id}/voice").json()

    assert voice["style"] is None
    assert voice["compass"] is None
    assert voice["core_values"] == []


def test_setting_the_compass_requires_ownership(client: TestClient) -> None:
    persona_id = _own_persona(client, uuid4())

    res = client.put(
        f"/personas/{persona_id}/moral-compass",
        headers=_headers(uuid4()),  # 남의 페르소나
        json={"standard": "내 맘대로"},
    )

    assert res.status_code == 403


def test_owner_sets_the_compass(client: TestClient) -> None:
    owner = uuid4()
    persona_id = _own_persona(client, owner)

    res = client.put(
        f"/personas/{persona_id}/moral-compass",
        headers=_headers(owner),
        json={
            "standard": "관계가 회복될 수 있는지를 먼저 본다",
            "rule_adherence": "약속은 지켜야 하지만 사정을 먼저 듣는다",
        },
    )

    assert res.status_code == 200, res.text
    voice = client.get(f"/personas/{persona_id}/voice").json()  # 공개 조회
    assert voice["compass"]["standard"] == "관계가 회복될 수 있는지를 먼저 본다"
    assert voice["compass"]["fairness"] == ""


def test_setting_the_compass_leaves_the_style_alone(client: TestClient) -> None:
    """축마다 PUT이 따로인 이유 — 하나를 손볼 때 다른 하나가 지워지면 안 된다."""
    owner = uuid4()
    persona_id = _own_persona(client, owner)
    client.put(
        f"/personas/{persona_id}/style",
        headers=_headers(owner),
        json={"tone": "따뜻하고 나긋한"},
    )

    voice = client.put(
        f"/personas/{persona_id}/moral-compass",
        headers=_headers(owner),
        json={"standard": "사실관계부터 본다"},
    ).json()

    assert voice["style"]["tone"] == "따뜻하고 나긋한"
    assert voice["compass"]["standard"] == "사실관계부터 본다"


def test_a_compass_without_a_standard_is_rejected(client: TestClient) -> None:
    # 판단 기준이 나침반의 앵커다 — 나머지 축만으로는 문단이 성립하지 않는다.
    owner = uuid4()
    persona_id = _own_persona(client, owner)

    res = client.put(
        f"/personas/{persona_id}/moral-compass",
        headers=_headers(owner),
        json={"rule_adherence": "사정을 먼저 듣는다"},
    )

    assert res.status_code == 422


def test_duplicate_core_values_are_rejected(client: TestClient) -> None:
    # 같은 가치를 두 번 매다는 것은 우선순위를 두 개 갖겠다는 뜻이라 말이 안 된다.
    owner = uuid4()
    persona_id = _own_persona(client, owner)

    res = client.put(
        f"/personas/{persona_id}/core-values",
        headers=_headers(owner),
        json={"values": ["정직", "정직"]},
    )

    # ValidationError → 422 (프로젝트 공통 매핑)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "duplicate_core_value"


def test_directness_is_bounded(client: TestClient) -> None:
    owner = uuid4()
    persona_id = _own_persona(client, owner)

    res = client.put(
        f"/personas/{persona_id}/style",
        headers=_headers(owner),
        json={"tone": "따뜻한", "directness": 9},
    )

    assert res.status_code == 422


def test_voice_of_unknown_persona_is_not_found(client: TestClient) -> None:
    assert client.get(f"/personas/{uuid4()}/voice").status_code == 404


# --- 방송 주제 (#75) ---------------------------------------------------------
#
# **이 절이 지키는 것: 인격과 도메인이 프롬프트에서 갈려 있다.**
#
# 전에는 모든 페르소나 앞에 "AI 연애상담 스트리머"가 박혀 있었다. 그러면 말투·가치관·
# 나침반을 아무리 정성껏 넣어도 앞줄이 주제를 고정하고, 학습 데이터에서 페르소나와
# 도메인을 교차시키는 것이 불가능해진다(`docs/persona-modeling.md`).


def test_no_domain_is_hardcoded_into_the_prompt() -> None:
    """어떤 페르소나도 기본적으로 상담사가 아니다."""
    assert "연애" not in DEFAULT_SYSTEM
    assert "상담" not in DEFAULT_SYSTEM
    assert "연애" not in system_message(_profile(tone="따뜻한")).content


def test_the_topic_comes_from_the_broadcast_not_the_persona() -> None:
    """같은 페르소나가 방송마다 다른 주제를 할 수 있다 — 그게 이 단위의 전부다."""
    profile = _profile(tone="따뜻하고 나긋한")

    counselling = system_message(profile, "연애 상담").content
    gaming = system_message(profile, "게임 방송").content

    assert "연애 상담" in counselling
    assert "게임 방송" in gaming
    # 인격은 그대로다.
    assert "따뜻하고 나긋한" in counselling
    assert "따뜻하고 나긋한" in gaming


def test_the_broadcast_is_its_own_paragraph_not_part_of_the_persona() -> None:
    """ "너는 누구인가"와 "오늘 무엇을 하는가"를 한 덩어리로 주지 않는다.

    섞으면 모델이 주제를 인격의 일부로 읽고, 그러면 페르소나를 바꾸지 않는 한 주제를
    못 바꾸는 상태로 되돌아간다 — 정확히 이번 단위가 없애려는 것이다.
    """
    content = system_message(_profile(tone="따뜻한"), "게임 방송").content

    assert "[이번 방송]" in content
    # 머리말이 인격 줄들보다 뒤에 온다.
    assert content.index("말투:") < content.index("[이번 방송]")


def test_a_broadcast_without_a_topic_leaves_no_dangling_header() -> None:
    """주제 없는 방송은 정상이다 — 빈 머리말을 남기지 않는다."""
    content = system_message(_profile(tone="따뜻한")).content

    assert "[이번 방송]" not in content
    assert "주제:" not in content


def test_a_topic_alone_does_not_make_a_persona() -> None:
    """주제만 있고 인격이 없으면 폴백이다.

    주제 한 줄로 인격을 대신하면 "주제 = 인격"이라는 등식을 프롬프트가 다시 만든다.
    """
    assert system_message(_profile(), "게임 방송").content == DEFAULT_SYSTEM


# --- 배선: 워커가 방을 읽는다 ------------------------------------------------


async def test_the_worker_reads_the_topic_at_consume_time() -> None:
    """주제는 요청 페이로드가 아니라 **소비 시점의 방**에서 온다.

    페이로드에 실으면 큐에 남아 있던 옛 요청이 옛 주제로 답한다 — 프로필을 소비
    시점에 읽는 것과 같은 함정이고, 그래서 같은 방식으로 피한다.
    """
    from fakeredis import FakeAsyncRedis, FakeServer
    from generation_harness import RecordingTranscript, StubProfiles, StubRooms

    from aria.common.tracing import NoOpTracing
    from aria.contexts.chat.adapter.outbound.redis.coordinator import (
        RedisResponseCoordinator,
    )
    from aria.contexts.chat.application.generation import (
        GenerationRequest,
        ResponseGenerationService,
    )
    from aria.contexts.chat.application.port.out.llm import LLMResult
    from aria.contexts.chat.domain.source import ChatSource

    seen: list[str] = []

    class _CapturingLLM:
        async def generate(self, persona_id, messages, params=None):
            seen.append(messages[0].content)
            return LLMResult(text="응답", model_version="stub-1")

    class _NullBroadcaster:
        async def publish(self, room_id, frame) -> None: ...

    room = uuid4()
    rooms = StubRooms("게임 방송")
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)

    await ResponseGenerationService(
        coordinator=RedisResponseCoordinator(redis),
        llm=_CapturingLLM(),
        broadcaster=_NullBroadcaster(),
        profiles=StubProfiles(_profile(tone="장난기 있는")),
        tracing=NoOpTracing(),
        transcript=RecordingTranscript(),
        rooms=rooms,
    ).handle(GenerationRequest.create(room, uuid4(), ChatSource.IDLE, "안녕"))

    assert rooms.asked == [room]  # 요청이 아니라 방에서 읽었다
    assert "게임 방송" in seen[0]
    assert "장난기 있는" in seen[0]


async def test_an_unreadable_room_does_not_stop_the_broadcast() -> None:
    """방을 못 읽어도 인격은 이미 프로필에서 나왔다 — 주제 한 줄 때문에 침묵하지 않는다."""
    from fakeredis import FakeAsyncRedis, FakeServer
    from generation_harness import RecordingTranscript, StubProfiles, StubRooms

    from aria.common.tracing import NoOpTracing
    from aria.contexts.chat.adapter.outbound.redis.coordinator import (
        RedisResponseCoordinator,
    )
    from aria.contexts.chat.application.generation import (
        GenerationRequest,
        ResponseGenerationService,
    )
    from aria.contexts.chat.domain.source import ChatSource

    published: list[dict] = []

    class _RecordingBroadcaster:
        async def publish(self, room_id, frame) -> None:
            published.append(frame)

    from aria.contexts.chat.adapter.outbound.inference.stub import StubPersonaLLM

    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)

    await ResponseGenerationService(
        coordinator=RedisResponseCoordinator(redis),
        llm=StubPersonaLLM(),
        broadcaster=_RecordingBroadcaster(),
        profiles=StubProfiles(_profile(tone="따뜻한")),
        tracing=NoOpTracing(),
        transcript=RecordingTranscript(),
        rooms=StubRooms(missing=True),
    ).handle(GenerationRequest.create(uuid4(), uuid4(), ChatSource.IDLE, "안녕"))

    assert [f["type"] for f in published] == ["reply"]
