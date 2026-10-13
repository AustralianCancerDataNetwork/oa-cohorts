"""Regression cases derived from the secure no-surgery eligibility failure."""
from datetime import timedelta

import pytest
import sqlalchemy as sa

from oa_cohorts.core import RuleCombination
from oa_cohorts.query.measure import MeasureMember
from tests.resolution import candidate_runtime as rt


def combine(kind, *children):
    root = rt.leaf([])
    root.subquery, root.children, root.combination = None, list(children), kind
    return root


@pytest.mark.parametrize("grain", ["episode", "patient"])
@pytest.mark.parametrize("window", [False, True])
def test_no_surgery_gates_stage_without_inventing_a_surgery_date(grain, window):
    stage = rt.leaf([{"source_event_id": 1, "measure_date": 9}])
    absence = rt.leaf([{"source_table": "procedure_occurrence", "measure_date": None,
                        "date_required": False}], 2)
    with sa.create_engine("sqlite://").connect() as db:
        den = rt.members(combine(RuleCombination.rule_and, stage, absence), db)
        num = rt.members(rt.leaf([{"source_event_id": 3, "measure_date": 19}], 3), db)
        assert len(den) == 2
        assert {m.completion_date for m in den} == {rt.EPOCH + timedelta(days=9)}
        assert next(m for m in den if m.source_table == "procedure_occurrence").source_date is None
        policy = {"numerator_reference": "denominator", "numerator_max_days_post": 10} if window else {}
        rows = rt.indicator_results(den, num, [MeasureMember(1, 10, 10, rt.EPOCH)], grain=grain, **policy)
        assert len(rows) == 1 and rows[0].denominator_value and rows[0].numerator_value
        if window:
            outside = rt.members(rt.leaf([{"measure_date": 20}], 4), db)
            assert not rt.indicator_results(den, outside, [MeasureMember(1, 10, 10, rt.EPOCH)], grain=grain, **policy)[0].numerator_value


@pytest.mark.parametrize("optional", [False, True])
def test_missing_required_event_date_remains_unresolved(optional):
    dated = rt.leaf([{"measure_date": 9}])
    missing = rt.leaf([{"measure_date": None, "date_required": optional is False}], 2)
    with sa.create_engine("sqlite://").connect() as db:
        den = rt.members(combine(RuleCombination.rule_and, dated, missing), db)
        rows = rt.indicator_results(den, [], [MeasureMember(1, 10, 10, rt.EPOCH)])
        assert rows[0].eligibility_status == ("eligible" if optional else "unresolved")


def test_timeless_or_alternative_does_not_delay_first_completion():
    stage = rt.leaf([{"measure_date": 9}])
    absence = rt.leaf([{"measure_date": None, "date_required": False}], 2)
    later = rt.leaf([{"source_event_id": 3, "measure_date": 30}], 3)
    with sa.create_engine("sqlite://").connect() as db:
        alternatives = combine(RuleCombination.rule_or, absence, later)
        rows = rt.members(combine(RuleCombination.rule_and, stage, alternatives), db)
        assert min(m.completion_date for m in rows) == rt.EPOCH + timedelta(days=9)
        assert {m.source_event_id for m in rows} == {None, 3}
        all_absence = combine(RuleCombination.rule_and, absence, absence)
        assert all(m.measure_date is None and not m.date_required for m in rt.members(all_absence, db))
        nested = rt.members(combine(RuleCombination.rule_and, stage, all_absence), db)
        assert {m.measure_date for m in nested} == {rt.EPOCH + timedelta(days=9)}
        timed = rt.window_node(absence, later, 0, 40, "all")
        assert rt.members(timed, db) == []


def test_absence_does_not_propagate_to_another_primary():
    stage = rt.leaf([{"episode_id": e, "measure_resolver": e, "measure_date": 9} for e in (10, 20)])
    absence = rt.leaf([{"measure_date": None, "date_required": False}], 2)
    with sa.create_engine("sqlite://").connect() as db:
        assert {m.episode_id for m in rt.members(combine(RuleCombination.rule_and, stage, absence), db)} == {10}
