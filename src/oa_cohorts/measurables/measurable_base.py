from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, TypeAlias

import sqlalchemy as sa
from sqlalchemy.orm.attributes import QueryableAttribute
from sqlalchemy.sql import ColumnElement

from ..core import RuleTarget, RuleTemporality

SQLCol: TypeAlias = sa.Column[Any] | ColumnElement[Any]

#: What a measurable spec is allowed to name. ``QueryableAttribute`` covers the
#: mapped-class case (``SomeMV.person_id``); ``ColumnElement`` covers plain Core
#: columns and hand-built SQL expressions, which the derived window measurables
#: and the test fixtures both use.
_COLUMN_LIKE: tuple[type, ...] = (QueryableAttribute, ColumnElement)

class MeasurableDomain(str, enum.Enum):
    dx = "dx"
    tx = "tx"
    meas = "meas"
    obs = "obs"
    proc = "proc"
    person = "person"
    visit = "visit"

@dataclass(frozen=True)
class MeasurableSpec:
    """
    Declarative mapping from a measurable ORM class to the query engine contract.

    Attribute names are stored as strings and resolved against the concrete class
    during binding. Optional value attributes remain unset when the measurable
    does not support that filter style.
    """
    domain: MeasurableDomain
    label: str

    episode_id_attr: str
    person_id_attr: str
    event_date_attr: str

    value_numeric_attr: str | None = None
    value_concept_attr: str | None = None
    value_string_attr: str | None = None 
    value_predicate_attr: str | None = None 

    temporality_map: Mapping[RuleTemporality, str] | None = None
    valid_targets: set[RuleTarget] | None = None

    def _resolve(self, cls: type[Any], field: str, attr: str) -> SQLCol:
        """Resolve one declared attribute name to a column on ``cls``.

        Both failure modes are caught here rather than downstream. A name that
        does not exist is obvious enough on its own, but a name that resolves to
        a method or a plain value binds silently and only fails much later when
        the query builder calls ``.label()`` on it — a long way from the
        declaration that caused it.
        """
        try:
            resolved = getattr(cls, attr)
        except AttributeError:
            available = sorted(
                name for name in dir(cls)
                if not name.startswith("_") and isinstance(getattr(cls, name, None), _COLUMN_LIKE)
            )
            raise AttributeError(
                f"{cls.__name__}.__measurable__ declares {field}={attr!r}, "
                f"which is not an attribute of {cls.__name__}. "
                f"Available columns: {', '.join(available) or '(none)'}"
            ) from None

        if not isinstance(resolved, _COLUMN_LIKE):
            raise TypeError(
                f"{cls.__name__}.__measurable__ declares {field}={attr!r}, which resolves to "
                f"{type(resolved).__name__} rather than a column. Measurable specs must name "
                "mapped columns or SQL expressions."
            )
        return resolved

    def bind(self, cls: type[Any]) -> BoundMeasurableSpec:
        """
        Resolve declared attribute names against a concrete measurable class.

        Unsupported value channels are left as ``None`` rather than replaced
        with synthetic SQL expressions. Downstream query logic must therefore
        explicitly check whether a measurable supports concept, numeric, string,
        or predicate filtering before building the corresponding rule.

        Called from ``MeasurableBase.__init_subclass__``, so every declared
        measurable is validated at import time rather than when a report first
        tries to compile it.
        """
        return BoundMeasurableSpec(
            domain=self.domain,
            label=self.label,
            episode_id_col=self._resolve(cls, "episode_id_attr", self.episode_id_attr),
            person_id_col=self._resolve(cls, "person_id_attr", self.person_id_attr),
            event_date_col=self._resolve(cls, "event_date_attr", self.event_date_attr),
            value_numeric_col=(
                self._resolve(cls, "value_numeric_attr", self.value_numeric_attr)
                if self.value_numeric_attr else None
            ),
            value_concept_col=(
                self._resolve(cls, "value_concept_attr", self.value_concept_attr)
                if self.value_concept_attr else None
            ),
            value_string_col=(
                self._resolve(cls, "value_string_attr", self.value_string_attr)
                if self.value_string_attr else None
            ),
            value_predicate_col=(
                self._resolve(cls, "value_predicate_attr", self.value_predicate_attr)
                if self.value_predicate_attr else None
            ),
            temporality_map={
                temporality: self._resolve(cls, f"temporality_map[{temporality.name}]", attr)
                for temporality, attr in (self.temporality_map or {}).items()
            },
            valid_targets=self.valid_targets,
        )

@dataclass(frozen=True)
class BoundMeasurableSpec:
    """Concrete SQLAlchemy column bindings derived from a :class:`MeasurableSpec`."""
    domain: MeasurableDomain
    label: str

    episode_id_col: SQLCol
    person_id_col: SQLCol
    event_date_col: SQLCol

    value_numeric_col: SQLCol | None = None
    value_concept_col: SQLCol | None = None
    value_string_col: SQLCol | None = None
    value_predicate_col: SQLCol | None = None

    temporality_map: Mapping[RuleTemporality, SQLCol] | None = None
    valid_targets: set[RuleTarget] | None = None

class MeasurableBase:
    """
    Base class for any MV/ORM entity that participates in measure logic.

    Subclasses provide a ``__measurable__`` spec describing which columns map
    onto the engine's canonical person / episode / date / value contract.
    """

    __measurable__: ClassVar[MeasurableSpec]
    __bound_measurable__: ClassVar[BoundMeasurableSpec]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        spec = getattr(cls, "__measurable__", None)
        if spec is not None:
            cls.__bound_measurable__ = spec.bind(cls)

    @classmethod
    def episode_id_col(cls) -> SQLCol:
        return cls.__bound_measurable__.episode_id_col

    @classmethod
    def person_id_col(cls) -> SQLCol:
        return cls.__bound_measurable__.person_id_col

    @classmethod
    def event_date_col(cls):
        return cls.__bound_measurable__.event_date_col
    

    @classmethod
    def temporal_anchor(cls, temporality: RuleTemporality):
        tm = cls.__bound_measurable__.temporality_map
        if tm and temporality in tm:
            return tm[temporality]
        return cls.event_date_col()
    
    # TODO: confirm removal of episode override logic in favour of 
    # linking all events to episodes at the data level through MVs
    # and remove ep_override args from all methods
    @classmethod
    def table_selectables(cls, ep_override: bool = False):
        return (
            cls.person_id_col().label("person_id"),
            cls.episode_id_col().label("episode_id"),
            cls.episode_id_col().label("measure_resolver"),
        )

    @classmethod
    def filter_table(cls, ep_override: bool = False):
        return cls.table_selectables(ep_override)

    @classmethod
    def filter_table_dated(cls, temporality: RuleTemporality, ep_override: bool = False):
        return (
            *cls.filter_table(ep_override),
            cls.temporal_anchor(temporality).label("measure_date"),
        )
