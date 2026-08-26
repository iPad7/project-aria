"""PersonaLLMPort — the app/inference boundary. See docs/architecture.md.

The streaming app knows *only* this contract. It passes a `persona_id` (who
should speak) and never a model version — version resolution lives behind the
boundary, in the ML platform's model registry alias (e.g. ``홍세현:prod``).

Rules baked into this port:
  1. Pass ``persona_id``, not a model version.
  2. ``LLMResult.model_version`` comes back as opaque telemetry. Log it for
     tracing but MUST NOT branch on it.
  3. The OpenAI fallback hides behind this same port — the app cannot tell an
     SFT/DPO sLLM apart from GPT-4o.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class GenParams:
    max_tokens: int = 512
    temperature: float = 0.2


@dataclass(frozen=True)
class LLMResult:
    text: str
    model_version: str | None = None  # opaque telemetry; do not branch on it


class PersonaLLMPort(Protocol):
    """Outbound port implemented by adapters in ``aria.adapter.outbound``."""

    async def generate(
        self,
        persona_id: str,
        messages: Sequence[Message],
        params: GenParams | None = None,
    ) -> LLMResult: ...
