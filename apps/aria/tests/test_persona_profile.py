"""페르소나 해석 — 말투·가치관이 응답에 반영되는가.

이 단위의 위험은 넷이다:

1. **프롬프트 합성** — 우선순위가 사라지거나, 숫자 척도가 뜻 없이 들어가거나.
2. **폴백** — 프로필 없는 기존 페르소나가 생성에서 죽는 것.
3. **경계** — 어댑터가 페르소나를 조회하게 되어 앱/추론 경계가 무너지는 것.
4. **교체 의미론** — 가치관 목록이 부분 수정처럼 동작해 우선순위가 밀리는 것.
"""

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
from aria.contexts.persona.domain.model import CommunicationStyle, Persona

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
    assert voice["core_values"] == []


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
