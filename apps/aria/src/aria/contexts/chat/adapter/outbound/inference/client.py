"""HTTP adapter for :class:`PersonaLLMPort` — talks to the inference serving.

Skeleton only. Resolves ``persona_id`` -> inference base URL from config; the
model *version* is decided on the far side by the registry alias, never here.
OpenAI fallback belongs behind this same port (compose it here or as a
decorating adapter) so the app stays oblivious. See docs/architecture.md.
"""

from __future__ import annotations

from typing import Sequence

import httpx

from aria.contexts.chat.application.port.out.llm import GenParams, LLMResult, Message


class InferenceHttpClient:
    """Implements PersonaLLMPort. TODO: fill in once the inference contract is pinned."""

    def __init__(self, base_urls: dict[str, str], timeout: float = 30.0) -> None:
        self._base_urls = base_urls
        self._timeout = timeout

    async def generate(
        self,
        persona_id: str,
        messages: Sequence[Message],
        params: GenParams | None = None,
    ) -> LLMResult:
        base_url = self._base_urls.get(persona_id)
        if base_url is None:
            # No dedicated sLLM for this persona yet -> OpenAI fallback (behind port).
            raise NotImplementedError("OpenAI fallback adapter not wired yet")

        _ = httpx  # placeholder until the request body/streaming contract is set
        raise NotImplementedError("inference request contract not pinned yet")
