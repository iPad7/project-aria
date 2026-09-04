"""LLM 관측 (NFR-OBS-2).

이 단위의 위험은 셋이다:

1. **관측이 생성을 죽이는 것.** 백엔드가 죽었다고 방송이 멈추면 관측이 장애 원인이 된다.
2. **맥락 누락.** 어댑터만 계측하면 어느 방·무엇이 촉발했는지가 안 보인다.
3. **켜지 않았는데 도는 것.** 키 없는 로컬·CI가 no-op로 그대로 통과해야 한다.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from generation_harness import StubProfiles

from aria.common.config import settings
from aria.common.langfuse_tracing import LangfuseTracing, build_tracing
from aria.common.persona_profile import PersonaProfile
from aria.common.tracing import NoOpTracing
from aria.contexts.chat.adapter.outbound.inference.traced import TracedPersonaLLM
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.application.generation import (
    GenerationRequest,
    ResponseGenerationService,
)
from aria.contexts.chat.application.port.out.llm import GenParams, LLMResult, Message
from aria.contexts.chat.domain.source import ChatSource


class _Recorded:
    def __init__(self, name: str, kind: str, input: Any, metadata: dict) -> None:
        self.name = name
        self.kind = kind
        self.input = input
        self.metadata = dict(metadata or {})
        self.output: Any = None


class _RecordingTracing:
    """`TracingPort` 스텁. 무엇이 어떤 순서로 기록됐는지 본다."""

    def __init__(self) -> None:
        self.observations: list[_Recorded] = []
        self.flushes = 0

    @contextmanager
    def observe(
        self,
        name: str,
        *,
        kind: str = "span",
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
        model: str | None = None,
    ) -> Iterator[Any]:
        recorded = _Recorded(name, kind, input, dict(metadata or {}))
        self.observations.append(recorded)

        class _Handle:
            def set_output(self, output: Any) -> None:
                recorded.output = output

            def set_metadata(self, metadata: Mapping[str, Any]) -> None:
                recorded.metadata.update(metadata)

        yield _Handle()

    def flush(self) -> None:
        self.flushes += 1

    def named(self, name: str) -> _Recorded:
        return next(o for o in self.observations if o.name == name)


class _StubLLM:
    async def generate(self, persona_id, messages, params=None) -> LLMResult:
        return LLMResult(text="응답", model_version="stub-1")


class _PreemptingLLM:
    def __init__(self, coordinator: RedisResponseCoordinator, room_id: UUID) -> None:
        self._coordinator = coordinator
        self._room_id = room_id

    async def generate(self, persona_id, messages, params=None) -> LLMResult:
        await self._coordinator.try_acquire(self._room_id, ChatSource.SUPERCHAT)
        return LLMResult(text="밀려난 응답", model_version="stub-1")


class _NullBroadcaster:
    async def publish(self, room_id: UUID, event: dict) -> None: ...

    async def subscribe(self, room_id: UUID):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis(server=FakeServer(), decode_responses=True)


def _service(
    redis: FakeAsyncRedis,
    tracing: Any,
    *,
    llm: Any = None,
    profile: PersonaProfile | None = None,
) -> ResponseGenerationService:
    return ResponseGenerationService(
        coordinator=RedisResponseCoordinator(redis),
        llm=llm or _StubLLM(),
        broadcaster=_NullBroadcaster(),
        profiles=StubProfiles(profile),
        tracing=tracing,
    )


# --- 기본은 꺼져 있다 --------------------------------------------------------


def test_tracing_is_off_by_default() -> None:
    # 키 없는 로컬·CI가 여기로 돈다. 계측 코드가 `if enabled:`로 도배되지 않도록
    # 끄는 방법을 분기가 아니라 구현 교체로 둔다.
    assert isinstance(build_tracing(), NoOpTracing)


def test_enabling_without_keys_falls_back_to_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 설정 실수지만 죽이지는 않는다 — 폴백 LLM(NFR-REL-3)과 달리 관측은 없어도
    # 서비스가 성립한다.
    monkeypatch.setattr(settings, "langfuse_enabled", True)
    monkeypatch.setattr(settings, "langfuse_public_key", None)

    assert isinstance(build_tracing(), NoOpTracing)


def test_enabled_with_keys_builds_langfuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "langfuse_enabled", True)
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-test")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-test")

    assert isinstance(build_tracing(), LangfuseTracing)


async def test_noop_observation_accepts_everything(redis: FakeAsyncRedis) -> None:
    # no-op이 조용히 동작하는지. 여기서 터지면 CI 전체가 죽는다.
    await _service(redis, NoOpTracing()).handle(
        GenerationRequest.create(uuid4(), uuid4(), ChatSource.CHAT, "안녕")
    )


# --- 바깥 span: 어댑터가 못 보는 맥락 ----------------------------------------


async def test_outer_span_carries_the_context(redis: FakeAsyncRedis) -> None:
    """어댑터는 `persona_id`와 `messages`만 받는다 — 방도 촉발 원인도 모른다."""
    tracing = _RecordingTracing()
    request = GenerationRequest.create(uuid4(), uuid4(), ChatSource.STORY, "사연입니다")

    await _service(redis, tracing).handle(request)

    span = tracing.named("response:story")
    assert span.kind == "span"
    assert span.metadata["room_id"] == str(request.room_id)
    assert span.metadata["persona_id"] == str(request.persona_id)
    assert span.metadata["source"] == "story"
    assert span.metadata["msg_id"] == str(request.msg_id)
    assert span.output == "응답"
    assert span.metadata["outcome"] == "published"


async def test_span_name_distinguishes_the_source(redis: FakeAsyncRedis) -> None:
    tracing = _RecordingTracing()

    await _service(redis, tracing).handle(
        GenerationRequest.create(uuid4(), uuid4(), ChatSource.SUPERCHAT, "고마워요")
    )

    assert tracing.observations[0].name == "response:superchat"


async def test_persona_voice_presence_is_recorded(redis: FakeAsyncRedis) -> None:
    # #59에서 로그로만 남기던 것 — 이제 "몇 %가 프로필 없이 도는가"를 셀 수 있다.
    voiced = PersonaProfile(persona_id=uuid4(), name="세현", tone="따뜻한")
    tracing = _RecordingTracing()

    await _service(redis, tracing, profile=voiced).handle(
        GenerationRequest.create(uuid4(), uuid4(), ChatSource.CHAT, "안녕")
    )

    assert tracing.observations[0].metadata["has_persona_voice"] is True


async def test_missing_persona_voice_is_recorded(redis: FakeAsyncRedis) -> None:
    tracing = _RecordingTracing()

    await _service(redis, tracing, profile=None).handle(
        GenerationRequest.create(uuid4(), uuid4(), ChatSource.CHAT, "안녕")
    )

    assert tracing.observations[0].metadata["has_persona_voice"] is False


# --- 버려진 생성이 보이는가 --------------------------------------------------


async def test_preempted_generation_is_recorded_as_such(
    redis: FakeAsyncRedis,
) -> None:
    """**버려진 생성의 비용**이 보여야 한다 — LLM은 이미 호출됐다."""
    room = uuid4()
    coordinator = RedisResponseCoordinator(redis)
    tracing = _RecordingTracing()
    service = ResponseGenerationService(
        coordinator=coordinator,
        llm=_PreemptingLLM(coordinator, room),
        broadcaster=_NullBroadcaster(),
        profiles=StubProfiles(),
        tracing=tracing,
    )

    await service.handle(
        GenerationRequest(
            msg_id=uuid4(),
            room_id=room,
            persona_id=uuid4(),
            source=ChatSource.CHAT,
            prompt="안녕",
            requested_at=GenerationRequest.create(
                room, uuid4(), ChatSource.CHAT, "x"
            ).requested_at,
        )
    )

    assert tracing.observations[0].metadata["outcome"] == "preempted"


async def test_request_without_a_slot_is_recorded(redis: FakeAsyncRedis) -> None:
    # 슬롯을 못 잡은 요청도 트레이스에 남아야 "왜 답이 없었나"에 답할 수 있다.
    room = uuid4()
    coordinator = RedisResponseCoordinator(redis)
    await coordinator.try_acquire(room, ChatSource.SUPERCHAT)
    tracing = _RecordingTracing()

    await _service(redis, tracing).handle(
        GenerationRequest.create(room, uuid4(), ChatSource.CHAT, "안녕")
    )

    assert tracing.observations[0].metadata["outcome"] == "no_slot"


# --- 안쪽 generation: 호출 자체 ----------------------------------------------


async def test_generation_span_records_prompt_and_output() -> None:
    # 프롬프트 전문이 남아야 "페르소나 프롬프트가 먹히나"를 볼 수 있다.
    tracing = _RecordingTracing()
    llm = TracedPersonaLLM(_StubLLM(), tracing, name="llm:primary")

    await llm.generate(
        "persona-1",
        [
            Message(role="system", content="너는 따뜻한 상담가다"),
            Message(role="user", content="짝사랑 중이에요"),
        ],
        GenParams(max_tokens=128, temperature=0.5),
    )

    gen = tracing.named("llm:primary")
    assert gen.kind == "generation"
    assert gen.input[0] == {"role": "system", "content": "너는 따뜻한 상담가다"}
    assert gen.output == "응답"
    assert gen.metadata["model_version"] == "stub-1"
    assert gen.metadata["max_tokens"] == 128
    assert gen.metadata["temperature"] == 0.5


async def test_generation_nests_inside_the_response_span(
    redis: FakeAsyncRedis,
) -> None:
    # 바깥이 먼저 열려야 안쪽이 그 아래 달린다.
    tracing = _RecordingTracing()
    service = _service(
        redis, tracing, llm=TracedPersonaLLM(_StubLLM(), tracing, name="llm:primary")
    )

    await service.handle(
        GenerationRequest.create(uuid4(), uuid4(), ChatSource.CHAT, "안녕")
    )

    assert [o.name for o in tracing.observations] == ["response:chat", "llm:primary"]


# --- 관측이 생성을 죽이지 않는가 (가장 중요) ---------------------------------


class _BrokenTracing:
    """구간을 열 때마다 터지는 백엔드."""

    @contextmanager
    def observe(self, name, *, kind="span", input=None, metadata=None, model=None):
        raise ConnectionError("관측 백엔드 다운")
        yield  # pragma: no cover

    def flush(self) -> None:
        raise ConnectionError("관측 백엔드 다운")


class _BrokenClient:
    def start_as_current_observation(self, **kwargs: Any) -> Any:
        raise ConnectionError("관측 백엔드 다운")

    def flush(self) -> None:
        raise ConnectionError("관측 백엔드 다운")


def test_langfuse_adapter_swallows_start_failures() -> None:
    """어댑터가 예외를 삼켜야 한다 — 관측이 장애 원인이 되면 안 된다."""
    tracing = LangfuseTracing(_BrokenClient())

    with tracing.observe("x", kind="generation") as observation:
        observation.set_output("y")  # no-op 구간이라 조용히 넘어간다

    tracing.flush()  # 여기서도 안 터진다


def test_langfuse_adapter_swallows_update_failures() -> None:
    class _BrokenSpan:
        def update(self, **kwargs: Any) -> None:
            raise ConnectionError("끊김")

    class _Client:
        def start_as_current_observation(self, **kwargs: Any):
            from contextlib import nullcontext

            return nullcontext(_BrokenSpan())

    with LangfuseTracing(_Client()).observe("x") as observation:
        observation.set_output("y")
        observation.set_metadata({"a": 1})
