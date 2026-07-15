import uuid
from datetime import datetime

from sqlmodel import Field, Session, SQLModel, create_engine, select

from aria.common.persistence import TimestampMixin, UUIDMixin


class _Widget(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "widget_test"
    label: str = Field()


def test_mixins_populate_id_and_timestamps() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[_Widget.__table__])

    with Session(engine) as session:
        widget = _Widget(label="hello")
        session.add(widget)
        session.commit()
        session.refresh(widget)

        assert isinstance(widget.id, uuid.UUID)
        assert widget.id.version == 7
        assert isinstance(widget.created_at, datetime)
        assert isinstance(widget.updated_at, datetime)


def test_ids_are_distinct_per_row() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[_Widget.__table__])

    with Session(engine) as session:
        session.add_all([_Widget(label="a"), _Widget(label="b")])
        session.commit()
        ids = session.exec(select(_Widget.id)).all()

    assert len(set(ids)) == 2
