import uuid

import pytest
from pydantic import ValidationError as PydanticValidationError

from aria.common.domain import Entity


class Persona(Entity):
    name: str


def test_entity_auto_assigns_uuid7_id() -> None:
    p = Persona(name="아리아")
    assert isinstance(p.id, uuid.UUID)
    assert p.id.version == 7


def test_entity_id_is_frozen() -> None:
    p = Persona(name="아리아")
    with pytest.raises(PydanticValidationError):
        p.id = uuid.uuid4()


def test_entity_rejects_wrong_type_on_construction() -> None:
    with pytest.raises(PydanticValidationError):
        Persona(name=123)  # type: ignore[arg-type]


def test_entity_revalidates_on_assignment() -> None:
    p = Persona(name="아리아")
    with pytest.raises(PydanticValidationError):
        p.name = None  # type: ignore[assignment]


def test_entity_forbids_extra_fields() -> None:
    with pytest.raises(PydanticValidationError):
        Persona(name="아리아", unexpected="x")  # type: ignore[call-arg]
