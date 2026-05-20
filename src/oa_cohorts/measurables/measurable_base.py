from __future__ import annotations
from dataclasses import dataclass
from typing import ClassVar, Optional, Mapping, Any, TypeAlias
import sqlalchemy as sa
import enum
from typing import TypeAlias
from sqlalchemy.sql import ColumnElement
from ..core import RuleTarget, RuleTemporality

SQLCol: TypeAlias = sa.Column[Any] | ColumnElement[Any]

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

    value_numeric_attr: Optional[str] = None
    value_concept_attr: Optional[str] = None
    value_string_attr: Optional[str] = None 
    value_predicate_attr: Optional[str] = None 

    temporality_map: Mapping[RuleTemporality, str] | None = None
    valid_targets: set[RuleTarget] | None = None

    def bind(self, cls: type[Any]) -> "BoundMeasurableSpec":
        """
        Resolve declared attribute names against a concrete measurable class.

        Unsupported value channels are left as ``None`` rather than replaced
        with synthetic SQL expressions. Downstream query logic must therefore
        explicitly check whether a measurable supports concept, numeric, string,
        or predicate filtering before building the corresponding rule.
        """
        return BoundMeasurableSpec(
            domain=self.domain,
            label=self.label,
            episode_id_col=getattr(cls, self.episode_id_attr),
            person_id_col=getattr(cls, self.person_id_attr),
            event_date_col=getattr(cls, self.event_date_attr),
            value_numeric_col=getattr(cls, self.value_numeric_attr) if self.value_numeric_attr else None,
            value_concept_col=getattr(cls, self.value_concept_attr) if self.value_concept_attr else None,
            value_string_col=getattr(cls, self.value_string_attr) if self.value_string_attr else None,
            value_predicate_col=getattr(cls, self.value_predicate_attr) if self.value_predicate_attr else None,
            temporality_map={
                k: getattr(cls, v)
                for k, v in (self.temporality_map or {}).items()
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
