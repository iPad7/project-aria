"""OpenAICompatLLM 어댑터 단위 테스트.

AsyncOpenAI 클라이언트를 가짜로 주입해 네트워크 없이 검증한다 — 어댑터가
포트 Message를 OpenAI chat 포맷으로 옮기고 응답을 LLMResult로 되매핑하는지.
"""

from types import SimpleNamespace
from typing import Any

from aria.contexts.chat.adapter.outbound.inference.openai_compat import (
    OpenAICompatLLM,
)
from aria.contexts.chat.application.port.out.llm import GenParams, Message


class _FakeCompletions:
    def __init__(self, content: str | None, model: str) -> None:
        self._content = content
        self._model = model
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)], model=self._model
        )


def _client(completions: _FakeCompletions) -> Any:
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


async def test_generate_maps_result() -> None:
    completions = _FakeCompletions(content="  응답이야  ", model="ax-4.0-light")
    llm = OpenAICompatLLM(_client(completions), model="ax-4.0-light")

    result = await llm.generate("persona-1", [Message(role="user", content="안녕")])

    assert result.text == "응답이야"  # 공백 트림됨
    assert result.model_version == "ax-4.0-light"  # 응답이 돌려준 실제 모델


async def test_generate_forwards_messages_verbatim() -> None:
    """어댑터는 메시지를 **그대로** 넘긴다 — 시스템 프롬프트를 만들지 않는다.

    페르소나 해석은 application의 일이다(`chat/application/persona_prompt.py`).
    어댑터가 또 주입하면 시스템 메시지가 둘이 되고, 무엇보다 어댑터가 페르소나를
    조회하게 되면 "앱은 persona_id만 넘기고 모델 세부를 모른다"는 경계가 무너진다.
    """
    completions = _FakeCompletions(content="ok", model="m")
    llm = OpenAICompatLLM(_client(completions), model="m")

    await llm.generate(
        "p",
        [
            Message(role="system", content="너는 따뜻한 상담가다"),
            Message(role="user", content="첫 사연"),
            Message(role="assistant", content="음"),
        ],
    )

    sent = completions.calls[0]
    assert sent["model"] == "m"
    assert sent["messages"] == [
        {"role": "system", "content": "너는 따뜻한 상담가다"},
        {"role": "user", "content": "첫 사연"},
        {"role": "assistant", "content": "음"},
    ]


async def test_generate_does_not_invent_a_system_message() -> None:
    # 호출자가 안 넣으면 없는 채로 간다. 지금 유일한 호출자(생성 서비스)는 항상
    # 넣으므로 실제로는 벌거벗지 않는다.
    completions = _FakeCompletions(content="ok", model="m")

    await OpenAICompatLLM(_client(completions), model="m").generate(
        "p", [Message(role="user", content="사연")]
    )

    assert completions.calls[0]["messages"] == [{"role": "user", "content": "사연"}]


async def test_generate_forwards_default_genparams() -> None:
    completions = _FakeCompletions(content="ok", model="m")
    llm = OpenAICompatLLM(_client(completions), model="m")

    await llm.generate("p", [Message(role="user", content="x")])

    assert completions.calls[0]["max_tokens"] == GenParams().max_tokens
    assert completions.calls[0]["temperature"] == GenParams().temperature


async def test_generate_handles_empty_content() -> None:
    completions = _FakeCompletions(content=None, model="m")
    llm = OpenAICompatLLM(_client(completions), model="m")

    result = await llm.generate("p", [Message(role="user", content="x")])

    assert result.text == ""
