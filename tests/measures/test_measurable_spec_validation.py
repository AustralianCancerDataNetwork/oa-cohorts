"""Validation of the string column names in ``MeasurableSpec``.

A spec names its columns as strings, resolved with ``getattr`` against the
concrete class. ``MeasurableBase.__init_subclass__`` binds eagerly, so these
checks run when the module is imported rather than when a report first tries to
compile the measurable.

The bad-name case was always caught, if bluntly. The case that mattered was a
name that *resolves* to something which is not a column: that used to bind
silently and fail much later inside the query builder.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so

from oa_cohorts.core import RuleTemporality
from oa_cohorts.measurables.measurable_base import (
    MeasurableBase,
    MeasurableDomain,
    MeasurableSpec,
)

events = sa.table(
    "events",
    sa.column("person_id"),
    sa.column("episode_id"),
    sa.column("event_date"),
    sa.column("value_number"),
    sa.column("died_on"),
)


def _spec(**overrides) -> MeasurableSpec:
    fields = {
        "domain": MeasurableDomain.dx,
        "label": "Fixture",
        "person_id_attr": "person_id",
        "episode_id_attr": "episode_id",
        "event_date_attr": "event_date",
    }
    fields.update(overrides)
    return MeasurableSpec(**fields)


_COLUMNS = {name: events.c[name] for name in ("person_id", "episode_id", "event_date")}


def test_a_valid_spec_binds_every_declared_channel():
    cls = type(
        "Valid",
        (MeasurableBase,),
        {
            "__measurable__": _spec(value_numeric_attr="value_number"),
            **_COLUMNS,
            "value_number": events.c.value_number,
        },
    )
    bound = cls.__bound_measurable__

    assert bound.person_id_col is events.c.person_id
    assert bound.value_numeric_col is events.c.value_number
    # Unset channels stay None rather than becoming synthetic expressions.
    assert bound.value_string_col is None


def test_unknown_column_name_names_the_field_and_the_alternatives():
    with pytest.raises(AttributeError) as excinfo:
        type(
            "Typo",
            (MeasurableBase,),
            {"__measurable__": _spec(person_id_attr="persn_id"), **_COLUMNS},
        )

    message = str(excinfo.value)
    assert "person_id_attr='persn_id'" in message
    assert "Available columns:" in message
    assert "person_id" in message


def test_name_resolving_to_a_method_is_rejected_at_declaration():
    """The failure this check exists for.

    Previously this bound the function object and only surfaced deep in query
    construction as ``'function' object has no attribute 'label'``.
    """
    with pytest.raises(TypeError, match="resolves to function rather than a column"):
        type(
            "NotAColumn",
            (MeasurableBase,),
            {
                "__measurable__": _spec(person_id_attr="helper"),
                **_COLUMNS,
                "helper": lambda self: 1,
            },
        )


def test_name_resolving_to_a_plain_value_is_rejected():
    with pytest.raises(TypeError, match="resolves to int rather than a column"):
        type(
            "PlainValue",
            (MeasurableBase,),
            {"__measurable__": _spec(event_date_attr="fixed"), **_COLUMNS, "fixed": 7},
        )


def test_optional_channels_are_validated_too():
    with pytest.raises(AttributeError, match="value_numeric_attr='no_such_column'"):
        type(
            "BadOptional",
            (MeasurableBase,),
            {"__measurable__": _spec(value_numeric_attr="no_such_column"), **_COLUMNS},
        )


def test_temporality_map_entries_are_validated_and_named():
    with pytest.raises(AttributeError, match=r"temporality_map\[dt_death\]='date_of_death'"):
        type(
            "BadAnchor",
            (MeasurableBase,),
            {
                "__measurable__": _spec(
                    temporality_map={RuleTemporality.dt_death: "date_of_death"}
                ),
                **_COLUMNS,
            },
        )


def test_a_valid_temporality_map_binds():
    cls = type(
        "GoodAnchor",
        (MeasurableBase,),
        {
            "__measurable__": _spec(temporality_map={RuleTemporality.dt_death: "died_on"}),
            **_COLUMNS,
            "died_on": events.c.died_on,
        },
    )

    assert cls.temporal_anchor(RuleTemporality.dt_death) is events.c.died_on
    # Anything unmapped falls back to the event date.
    assert cls.temporal_anchor(RuleTemporality.dt_any) is events.c.event_date


class _Local(so.DeclarativeBase):
    pass


class _MappedEvent(_Local):
    """Stands in for a mapped materialised view, as production measurables use."""

    __tablename__ = "mapped_events"

    person_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    episode_id: so.Mapped[int]
    event_date: so.Mapped[str]


def test_mapped_class_attributes_are_accepted():
    """Production measurables name mapped columns, not Core columns.

    Those resolve to ``InstrumentedAttribute``, which is not a ``ColumnElement``
    — so the accepted-types tuple has to cover both.
    """
    Event = _MappedEvent
    cls = type(
        "Mapped",
        (MeasurableBase,),
        {
            "__measurable__": _spec(),
            "person_id": Event.person_id,
            "episode_id": Event.episode_id,
            "event_date": Event.event_date,
        },
    )

    assert cls.__bound_measurable__.person_id_col is Event.person_id


def test_sql_expressions_are_accepted():
    """Derived measurables build columns rather than naming table columns."""
    computed = sa.func.coalesce(events.c.value_number, 0)
    cls = type(
        "Computed",
        (MeasurableBase,),
        {
            "__measurable__": _spec(value_numeric_attr="computed"),
            **_COLUMNS,
            "computed": computed,
        },
    )

    assert cls.__bound_measurable__.value_numeric_col is computed
