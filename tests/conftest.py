from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from oa_cohorts.core import (
    RuleCombination,
    RuleMatcher,
    RuleTarget,
    RuleTemporality,
    ThresholdDirection,
)
from oa_cohorts.measurables.measurable_base import (
    MeasurableBase,
    MeasurableDomain,
    MeasurableSpec,
)
from oa_cohorts.query.dash_cohort import DashCohort
from oa_cohorts.query.indicator import Indicator
from oa_cohorts.query.measure import MeasureExecutor, MeasureMember
from oa_cohorts.query.query_rule import (
    ExactRule,
    PhenotypeRule,
    PresenceRule,
    ScalarRule,
)
from oa_cohorts.query.subquery import Subquery

os.environ.setdefault("ENGINE", "sqlite://")


test_events = sa.table(
    "test_events",
    sa.column("person_id"),
    sa.column("episode_id"),
    sa.column("event_date"),
    sa.column("concept_id"),
    sa.column("value_number"),
)


class ExecutableMeasurable(MeasurableBase):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.dx,
        label="Executable",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="event_date",
        value_concept_attr="concept_id",
        value_numeric_attr="value_number",
    )

    person_id = test_events.c.person_id
    episode_id = test_events.c.episode_id
    event_date = test_events.c.event_date
    concept_id = test_events.c.concept_id
    value_number = test_events.c.value_number


class NoNumericMeasurable(MeasurableBase):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.dx,
        label="No numeric",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="event_date",
        value_concept_attr="concept_id",
    )

    person_id = test_events.c.person_id
    episode_id = test_events.c.episode_id
    event_date = test_events.c.event_date
    concept_id = test_events.c.concept_id


@pytest.fixture(autouse=True)
def patch_measurable_registry(monkeypatch):
    registry = {
        RuleTarget.dx_any: ExecutableMeasurable,
        RuleTarget.meas_concept: NoNumericMeasurable,
    }
    monkeypatch.setattr("oa_cohorts.query.subquery.get_measurable_registry", lambda: registry)
    monkeypatch.setattr("oa_cohorts.query.query_rule.get_measurable_registry", lambda: registry)
    return registry


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, stmt):
        return FakeResult(self._rows)

    def rollback(self):
        return None


@dataclass
class FakeSubquery:
    query: sa.Select

    def get_subquery_any(self, *, ep_override: bool = False):
        return self.query

    def get_subquery_first(self, *, ep_override: bool = False):
        return self.query

    def get_subquery_undated(self, *, ep_override: bool = False):
        return self.query


@dataclass
class RaisingSubquery:
    def get_subquery_any(self, *, ep_override: bool = False):
        raise KeyError("invalid combination")

    def get_subquery_first(self, *, ep_override: bool = False):
        raise KeyError("invalid combination")

    def get_subquery_undated(self, *, ep_override: bool = False):
        raise KeyError("invalid combination")


@dataclass
class FakeMeasure:
    measure_id: int
    name: str
    combination: object = RuleCombination.rule_or
    subquery: object | None = None
    _children: list[object] = field(default_factory=list)
    person_ep_override: bool = False
    _members: list[MeasureMember] | None = None
    window_config: object = None

    @property
    def children(self) -> list[object]:
        return list(self._children)

    @property
    def is_temporal_window(self) -> bool:
        return self.window_config is not None

    def members(self, executor: MeasureExecutor):
        return executor.members(self)


@dataclass
class FakeDashCohortDefinition:
    dash_cohort_measure: object | None = None

    def members(self, executor: MeasureExecutor):
        if not self.dash_cohort_measure:
            return ()
        return self.dash_cohort_measure.members(executor)


class FakeIndicator:
    numerator_members = Indicator.numerator_members
    denominator_members = Indicator.denominator_members
    numerator_window = Indicator.numerator_window
    denominator_window = Indicator.denominator_window
    has_numerator_window = Indicator.has_numerator_window
    has_denominator_window = Indicator.has_denominator_window
    member_within_window = Indicator.member_within_window
    _window_for_side = Indicator._window_for_side
    _coerce_member_date = staticmethod(Indicator._coerce_member_date)
    _anchor_dates_by_key = Indicator._anchor_dates_by_key
    _matches_any_cohort_anchor = Indicator._matches_any_cohort_anchor
    _uses_full_cohort_denominator = Indicator._uses_full_cohort_denominator


class FakeCohort:
    members = DashCohort.members

    def __init__(self, definitions):
        self.definitions = definitions


class FixedMembersMeasure:
    def __init__(self, members):
        self._members = members

    def members(self, executor: MeasureExecutor):
        return list(self._members)


@pytest.fixture
def db():
    return FakeDb(
        [
            SimpleNamespace(
                person_id=1,
                episode_id=10,
                measure_resolver=10,
                measure_date=date(2024, 1, 1),
            )
        ]
    )


@pytest.fixture
def executor(db):
    return MeasureExecutor(db)


@pytest.fixture
def definition_missing_measure():
    return FakeDashCohortDefinition(dash_cohort_measure=None)


@pytest.fixture
def cohort():
    # Mirrors the real duplicate dash_cohort_def -> dash_cohort mappings present
    # in dash_config/dash_cohort_def_map.csv so the deduplication test exercises
    # a shape that actually appears in configuration data.
    duplicate_member = MeasureMember(person_id=1, measure_resolver=66, episode_id=66, measure_date=date(2024, 1, 1))
    unique_member = MeasureMember(person_id=2, measure_resolver=67, episode_id=67, measure_date=date(2024, 1, 2))
    definition_a = FakeDashCohortDefinition(FixedMembersMeasure([duplicate_member, unique_member]))
    definition_b = FakeDashCohortDefinition(FixedMembersMeasure([duplicate_member]))
    return FakeCohort([definition_a, definition_b])


@pytest.fixture
def indicator():
    value = FakeIndicator()
    value.numerator_measure = FakeMeasure(measure_id=1, name="numerator")
    value.denominator_measure = FakeMeasure(measure_id=2, name="denominator")
    return value


@pytest.fixture
def measure_zero():
    return FakeMeasure(measure_id=0, name="full cohort")


@pytest.fixture
def measure_leaf():
    query = sa.select(
        sa.literal(1).label("person_id"),
        sa.literal(10).label("episode_id"),
        sa.literal(10).label("measure_resolver"),
        sa.literal(date(2024, 1, 1)).label("measure_date"),
    )
    return FakeMeasure(measure_id=10, name="leaf", subquery=FakeSubquery(query))


@pytest.fixture
def measure_empty():
    return FakeMeasure(measure_id=11, name="empty", subquery=None, _children=[])


@pytest.fixture
def measure_with_bad_child():
    bad_query = sa.select(
        sa.literal(1).label("person_id"),
    )
    bad_child = FakeMeasure(measure_id=12, name="bad child", subquery=FakeSubquery(bad_query))
    return FakeMeasure(measure_id=13, name="parent", combination=RuleCombination.rule_or, _children=[bad_child])


@pytest.fixture
def measure_invalid_combination():
    return FakeMeasure(measure_id=14, name="invalid", subquery=RaisingSubquery())


@pytest.fixture
def subquery_empty():
    return Subquery(
        subquery_id=1,
        name="empty",
        short_name="empty",
        target=RuleTarget.dx_any,
        temporality=RuleTemporality.dt_any,
        rules=[],
    )


@pytest.fixture
def subquery_unknown_target():
    return Subquery(
        subquery_id=2,
        name="unknown",
        short_name="unknown",
        target=RuleTarget.dx_first_primary,
        temporality=RuleTemporality.dt_any,
        rules=[
            PresenceRule(
                query_rule_id=1,
                matcher=RuleMatcher.presence,
                concept_id=0,
            )
        ],
    )


@pytest.fixture
def subquery_scalar_rule():
    return Subquery(
        subquery_id=3,
        name="scalar",
        short_name="scalar",
        target=RuleTarget.meas_concept,
        temporality=RuleTemporality.dt_any,
        rules=[
            ScalarRule(
                query_rule_id=2,
                matcher=RuleMatcher.scalar,
                concept_id=0,
                scalar_threshold=5,
                threshold_direction=ThresholdDirection.gt,
            )
        ],
    )


@pytest.fixture
def rule_exact_missing():
    return ExactRule(
        query_rule_id=10,
        matcher=RuleMatcher.exact,
        concept_id=123,
    )


@pytest.fixture
def rule_scalar_missing():
    return ScalarRule(
        query_rule_id=11,
        matcher=RuleMatcher.scalar,
        concept_id=0,
        threshold_direction=ThresholdDirection.gt,
    )


@pytest.fixture
def rule_scalar_missing_comparator():
    return ScalarRule(
        query_rule_id=12,
        matcher=RuleMatcher.scalar,
        concept_id=0,
        scalar_threshold=1,
        threshold_direction=ThresholdDirection.gt,
    )


@pytest.fixture
def rule_phenotype_missing():
    return PhenotypeRule(
        query_rule_id=13,
        matcher=RuleMatcher.phenotype,
        phenotype_id=99,
    )
