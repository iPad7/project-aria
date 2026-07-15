import uuid

from aria.common.ids import new_id


def test_new_id_is_uuid7() -> None:
    generated = new_id()
    assert isinstance(generated, uuid.UUID)
    assert generated.version == 7


def test_new_id_is_time_ordered() -> None:
    # UUIDv7은 시간순 → 나중에 만든 게 더 크다(문자열 정렬 = 생성 순서)
    ids = [str(new_id()) for _ in range(50)]
    assert ids == sorted(ids)


def test_new_id_is_unique() -> None:
    assert len({new_id() for _ in range(1000)}) == 1000
