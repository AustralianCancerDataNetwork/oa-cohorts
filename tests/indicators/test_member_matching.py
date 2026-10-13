"""The membership API and report pivot must answer the same cancer question."""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from oa_cohorts.output.pivot_queries import build_pivot_indicators
from oa_cohorts.query.measure import MeasureExecutor, MeasureMember
from tests.conftest import FakeIndicator, FakeMeasure

DAY = date(2026, 1, 1)


def member(person=1, episode=10, resolver=None, day=0):
    return MeasureMember(person, episode if resolver is None else resolver, episode,
                         None if day is None else DAY + timedelta(days=day))


def evaluate(cohort, numerator, denominator=None, **options):
    indicator = FakeIndicator()
    indicator.indicator_id = 19
    indicator.numerator_measure_id = 30
    indicator.denominator_measure_id = 0 if denominator is None else 20
    indicator.numerator_measure = FakeMeasure(30, "numerator")
    indicator.denominator_measure = FakeMeasure(indicator.denominator_measure_id, "denominator")
    for key, value in options.items():
        setattr(indicator, key, value)
    executor = MeasureExecutor(None)
    executor._cache = {30: numerator, indicator.denominator_measure_id: denominator or []}
    report = SimpleNamespace(indicators=[indicator], cohorts=[])
    rows = build_pivot_indicators(report, executor, cohort_members=cohort)
    return indicator, executor, rows


@pytest.mark.parametrize("denominator", [None, [member(), member(episode=20), member(person=2)]])
def test_complete_key_prevents_cross_episode_and_cross_person_passes(denominator):
    cohort = [member(), member(episode=20), member(person=2)]
    indicator, executor, rows = evaluate(cohort, [member(day=5)], denominator)
    assert [row.numerator_value for row in rows] == [True, False, False]
    assert list(indicator.numerator_members(executor, cohort_members=cohort)) == [member(day=5)]


@pytest.mark.parametrize("whole", [True, False])
def test_episode_id_is_required_even_when_resolvers_are_shared(whole):
    cohort = [member(resolver=99), member(episode=20, resolver=99)]
    indicator, executor, rows = evaluate(cohort, [member(resolver=99, day=3)], None if whole else cohort)
    assert [row.numerator_value for row in rows] == [True, False]
    assert list(indicator.numerator_members(executor, cohort_members=cohort)) == [member(resolver=99, day=3)]


def test_whole_cohort_has_one_vote_and_earliest_reference():
    cohort = [member(), member(day=30)]
    evidence = [member(day=5), member(day=5)]
    indicator, executor, rows = evaluate(cohort + cohort, evidence,
                                        numerator_max_days_prior=0, numerator_max_days_post=10)
    assert len(rows) == 1
    assert [row.numerator_value for row in rows] == [True]
    assert indicator.numerator_members(executor, cohort_members=cohort) == [member(day=5)]
    assert len(indicator.denominator_members(executor, cohort_members=cohort + cohort)) == len(rows)


@pytest.mark.parametrize("whole,window,expected", [(True, False, False), (False, False, False),
                                                  (True, True, False), (False, True, False)])
def test_undated_evidence_preserves_report_policy(whole, window, expected):
    cohort = [member()]
    options = {"numerator_max_days_post": 10} if window else {}
    indicator, executor, rows = evaluate(cohort, [member(day=None)], None if whole else cohort, **options)
    assert rows[0].numerator_value is expected
    assert rows[0].numerator_date is None
    assert bool(indicator.numerator_members(executor, cohort_members=cohort)) is expected


def test_missing_episode_evidence_does_not_match_and_missing_scope_key_fails():
    indicator, executor, rows = evaluate([member()], [member(episode=None, resolver=10, day=3)])
    assert not rows[0].numerator_value
    assert not indicator.numerator_members(executor, cohort_members=[member()])
    with pytest.raises(ValueError, match="episode_id"):
        evaluate([member(episode=None, resolver=10)], [member()])


def test_whole_cohort_scope_is_explicit_even_when_measure_zero_is_cached():
    indicator, executor, _ = evaluate([member()], [member(day=3)])
    executor._cache[0] = [member()]
    for method in (indicator.numerator_members, indicator.denominator_members):
        with pytest.raises(ValueError, match="cohort_members"):
            method(executor)


def test_explicit_denominator_without_windows_matches_keys_not_dates():
    indicator, executor, _ = evaluate([member()], [member(day=3)], [member(day=1)])
    assert indicator.numerator_members(executor) == [member(day=3)]
    indicator.numerator_max_days_post = 10
    with pytest.raises(ValueError, match="cohort_members"):
        indicator.numerator_members(executor)


def test_explicit_undated_denominators_are_ineligible_in_both_outputs():
    indicator, executor, rows = evaluate([member()], [member(day=3)], [member(day=None)])
    assert len(rows) == 1 and rows[0].eligibility_status == "unresolved"
    assert not rows[0].denominator_value
    assert not indicator.denominator_members(executor, cohort_members=[member()])


@pytest.mark.parametrize("whole", [True, False])
@pytest.mark.parametrize("passing", [[], [member(day=3)], [member(episode=20, day=35)],
                                      [member(day=3), member(episode=20, day=35)]])
def test_patient_grain_reduces_only_after_same_cancer_matching(whole, passing):
    cohort = [member(), member(episode=20, day=30), member(person=2)]
    indicator, executor, rows = evaluate(cohort + cohort, passing + passing,
                                        None if whole else cohort, grain="patient")
    assert [(row.person_id, row.measure_resolver, row.grain) for row in rows] == [
        (1, 1, "patient"), (2, 2, "patient")]
    assert [row.numerator_value for row in rows] == [bool(passing), False]
    assert [row.denominator_date for row in rows] == [DAY, DAY]
    assert rows[0].numerator_date == (min(m.measure_date for m in passing) if passing else None)
    eligible = indicator.denominator_members(executor, cohort_members=cohort)
    assert len(eligible) == 2 and all(m.episode_id is None for m in eligible)
    numerator = indicator.numerator_members(executor, cohort_members=cohort)
    assert len(numerator) == int(bool(passing))
    assert [(m.person_id, m.measure_date) for m in numerator] == [
        (r.person_id, r.numerator_date) for r in rows if r.numerator_value]
    audit = indicator.patient_results(executor, cohort_members=cohort)[0]
    assert audit.eligible_keys == {(1, 10, 10), (1, 20, 20)}
    assert audit.passing_keys == {(1, m.episode_id, m.measure_resolver) for m in passing}


def test_patient_cannot_pass_on_an_ineligible_or_out_of_scope_cancer():
    cohort = [member(), member(episode=20)]
    evidence = [member(episode=20, day=3), member(episode=30, day=3)]
    indicator, executor, rows = evaluate(cohort, evidence, [member()], grain="patient")
    assert len(rows) == 1 and not rows[0].numerator_value
    assert not indicator.numerator_members(executor, cohort_members=cohort)
    assert evaluate(cohort, evidence, [], grain="patient")[2] == []


def test_patient_missing_death_date_is_not_eligible():
    indicator, executor, rows = evaluate([member()], [member(day=3)], [member(day=None)], grain="patient")
    assert len(rows) == 1 and rows[0].eligibility_status == "unresolved"
    assert not rows[0].denominator_value
    assert not indicator.denominator_members(executor, cohort_members=[member()])


def test_patient_reduction_uses_qualifying_row_window_and_dates():
    cohort = [member(), member(day=30), member(episode=20, day=30)]
    indicator, executor, rows = evaluate(cohort, [member(day=25), member(episode=20, day=35)],
                                        grain="patient", numerator_max_days_prior=0, numerator_max_days_post=10)
    assert len(rows) == 1
    assert rows[0].numerator_date == DAY + timedelta(days=35)
    assert indicator.numerator_members(executor, cohort_members=cohort)[0].measure_date == rows[0].numerator_date
    with pytest.raises(ValueError, match="cohort_members"):
        indicator.denominator_members(executor)


def test_reducer_rejects_patient_members_as_episode_input():
    from oa_cohorts.query.indicator_members import (
        IndicatorMemberResult,
        reduce_patient_results,
    )

    reduced = IndicatorMemberResult(member(episode=None, resolver=1), True, DAY)
    with pytest.raises(ValueError, match="episode-aligned"):
        reduce_patient_results([reduced])


@pytest.mark.parametrize("whole", [True, False])
@pytest.mark.parametrize("grain", ["episode", "patient"])
def test_matching_does_not_scan_other_cancers_for_each_member(monkeypatch, whole, grain):
    """Count work rather than time so a quadratic regression fails reliably."""
    import oa_cohorts.query.indicator_members as members_module

    cohort = [member(person=person, episode=episode, resolver=99)
              for person in range(256) for episode in (10, 20)]
    evidence = [member(person=person, resolver=99, day=5) for person in range(256)]
    original = members_module.member_key
    calls = []

    def counted_key(value):
        calls.append(None)
        return original(value)

    monkeypatch.setattr(members_module, "member_key", counted_key)
    indicator, executor, rows = evaluate(cohort, evidence, None if whole else cohort, grain=grain)
    work_limit = 20 * (len(cohort) + len(evidence))
    assert len(calls) < work_limit, "Pivot must only examine evidence for the matching cancer"
    assert sum(row.numerator_value for row in rows) == 256
    assert len(rows) == (512 if grain == "episode" else 256)
    calls.clear()
    numerator = indicator.numerator_members(executor, cohort_members=cohort)
    assert len(calls) < work_limit, "Membership must only examine matching cancer anchors/evidence"
    assert [(m.person_id, m.measure_date) for m in numerator] == [
        (r.person_id, r.numerator_date) for r in rows if r.numerator_value]
