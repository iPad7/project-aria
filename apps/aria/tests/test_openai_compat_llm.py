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


async def test_generate_prepends_system_and_forwards_messages() -> None:
    completions = _FakeCompletions(content="ok", model="m")
    llm = OpenAICompatLLM(_client(completions), model="m")

    await llm.generate(
        "p",
        [
            Message(role="user", content="첫 사연"),
            Message(role="assistant", content="음"),
        ],
    )

    sent = completions.calls[0]
    assert sent["model"] == "m"
    assert sent["messages"][0]["role"] == "system"  # 시스템 프롬프트가 맨 앞
    assert sent["messages"][1] == {"role": "user", "content": "첫 사연"}
    assert sent["messages"][2] == {"role": "assistant", "content": "음"}


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
