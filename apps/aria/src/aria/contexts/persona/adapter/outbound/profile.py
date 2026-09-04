"""PersonaProfilePort의 persona 구현.

`common.persona_profile`의 계약을 persona가 채운다. chat은 이 클래스를 모르고
persona도 chat을 모른다 — 배선은 generation-worker의 합성 루트가 한다.

**포트가 async인데 리포지토리는 sync다.** 소비자가 generation-worker의 async 경로라
`anyio.to_thread`로 넘긴다 — `CommunityStoryFeed`·`WalletSuperchat`·chat의 방
리포지토리와 같은 방식이다.
"""

from __future__ import annotations

from uuid import UUID

import anyio.to_thread

from aria.common.persona_profile import PersonaProfile
from aria.contexts.persona.application.port.out.repository import (
    PersonaRepository,
    ProfileRepository,
)


class PersonaProfileProvider:
    def __init__(
        self, personas: PersonaRepository, profiles: ProfileRepository
    ) -> None:
        self._personas = personas
        self._profiles = profiles

    async def profile_of(self, persona_id: UUID) -> PersonaProfile | None:
        return await anyio.to_thread.run_sync(self._build, persona_id)

    def _build(self, persona_id: UUID) -> PersonaProfile | None:
        persona = self._personas.get_by_id(persona_id)
        if persona is None:
            return None

        style = self._profiles.get_style(persona_id)
        return PersonaProfile(
            persona_id=persona.id,
            name=persona.name,
            description=persona.description,
            # 말투가 아직 없는 페르소나가 정상이다 — 그 경우 소비자가 폴백한다.
            tone=style.tone if style else None,
            sentence_length=style.sentence_length if style else None,
            question_style=style.question_style if style else None,
            directness=style.directness if style else None,
            empathy_expression=style.empathy_expression if style else None,
            core_values=tuple(self._profiles.list_core_values(persona_id)),
        )
