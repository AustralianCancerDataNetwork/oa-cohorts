from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from types import SimpleNamespace

from oa_cohorts.output.pivot_queries import build_pivot_indicators
from oa_cohorts.output.report_runner import ReportRunner
from oa_cohorts.query.measure import MeasureExecutor, MeasureMember
from oa_cohorts.query.report import Report


class FakeMeasure:
    def __init__(self, measure_id: int, name: str, members: list[MeasureMember]):
        self.measure_id = measure_id
        self.id = measure_id
        self.name = name
        self._seed_members = list(members)
        self._members = None
        self.combination = SimpleNamespace(value="OR")

    def members(self, executor: MeasureExecutor):
        return executor.members(self)


@dataclass
class FakeDefinition:
    dash_cohort_measure: FakeMeasure | None
    dash_cohort_def_name: str = "Definition"


@dataclass
class FakeCohort:
    definitions: list[FakeDefinition]
    dash_cohort_name: str = "Cohort"


@dataclass
class FakeReportCohort:
    cohort: FakeCohort


@dataclass
class FakeIndicator:
    indicator_id: int
    numerator_measure: FakeMeasure
    denominator_measure: FakeMeasure
    numerator_measure_id: int
    denominator_measure_id: int
    numerator_max_days_prior: int | None = None
    numerator_max_days_post: int | None = None
    denominator_max_days_prior: int | None = None
    denominator_max_days_post: int | None = None


class FakeReport:
    execute = Report.execute
    assert_executed = Report.assert_executed

    def __init__(
        self,
        *,
        cohorts: list[FakeReportCohort],
        indicators: list[FakeIndicator],
        indicator_measures: list[FakeMeasure],
        cohort_measures: list[FakeMeasure],
    ):
        self.cohorts = cohorts
        self.indicators = indicators
        self.indicator_measures = indicator_measures
        self.cohort_measures = cohort_measures
        self.report_name = "Test report"
        self.report_short_name = "TEST"
        self.report_description = "Test"

    def members(self, executor: MeasureExecutor):
        seen: set[MeasureMember] = set()
        out: list[MeasureMember] = []

        for report_cohort in self.cohorts:
            for definition in report_cohort.cohort.definitions:
                measure = definition.dash_cohort_measure
                if not measure:
                    continue
                for member in measure.members(executor):
                    if member in seen:
                        continue
                    seen.add(member)
                    out.append(member)

        return out


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return FakeScalarResult(self._rows)


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def execute(self, stmt):
        self.statements.append(stmt)
        return FakeExecuteResult(self.rows)


def seed_executor(executor: MeasureExecutor, *measures: FakeMeasure) -> MeasureExecutor:
    for measure in measures:
        executor._cache[measure.measure_id] = list(measure._seed_members)
        measure._members = list(measure._seed_members)
    return executor


def build_report(
    *,
    cohort_measure: FakeMeasure,
    numerator_measure: FakeMeasure,
    denominator_measure: FakeMeasure,
    **indicator_kwargs,
) -> FakeReport:
    cohort = FakeReportCohort(FakeCohort([FakeDefinition(cohort_measure)]))
    indicator = FakeIndicator(
        indicator_id=99,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
        numerator_measure_id=numerator_measure.measure_id,
        denominator_measure_id=denominator_measure.measure_id,
        **indicator_kwargs,
    )
    return FakeReport(
        cohorts=[cohort],
        indicators=[indicator],
        indicator_measures=[numerator_measure, denominator_measure],
        cohort_measures=[cohort_measure],
    )


def test_build_pivot_indicators_emits_one_row_per_denominator_member():
    cohort_measure = FakeMeasure(10, "cohort", [
        MeasureMember(person_id=1, measure_resolver=101, episode_id=101, measure_date=date(2024, 1, 1)),
        MeasureMember(person_id=2, measure_resolver=202, episode_id=202, measure_date=date(2024, 1, 2)),
        MeasureMember(person_id=3, measure_resolver=303, episode_id=303, measure_date=date(2024, 1, 3)),
    ])
    denominator_measure = FakeMeasure(20, "denominator", [
        MeasureMember(person_id=1, measure_resolver=101, episode_id=101, measure_date=date(2024, 2, 1)),
        MeasureMember(person_id=2, measure_resolver=202, episode_id=202, measure_date=date(2024, 2, 2)),
        MeasureMember(person_id=3, measure_resolver=303, episode_id=303, measure_date=date(2024, 2, 3)),
    ])
    numerator_measure = FakeMeasure(30, "numerator", [
        MeasureMember(person_id=2, measure_resolver=202, episode_id=202, measure_date=date(2024, 2, 5)),
        MeasureMember(person_id=99, measure_resolver=202, episode_id=202, measure_date=date(2024, 2, 6)),
    ])
    report = build_report(
        cohort_measure=cohort_measure,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
    )
    executor = seed_executor(MeasureExecutor(None), cohort_measure, denominator_measure, numerator_measure)

    rows = build_pivot_indicators(report, executor)

    assert len(rows) == 3
    assert [row.person_id for row in rows] == [1, 2, 3]
    assert [row.numerator_value for row in rows] == [False, True, False]
    assert [row.denominator_value for row in rows] == [True, True, True]
    assert [row.denominator_date for row in rows] == [
        date(2024, 2, 1),
        date(2024, 2, 2),
        date(2024, 2, 3),
    ]
    assert rows[1].numerator_date == date(2024, 2, 5)


def test_build_pivot_indicators_uses_full_report_cohort_for_measure_zero_denominator():
    cohort_measure = FakeMeasure(10, "cohort", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 1, 1)),
        MeasureMember(person_id=2, measure_resolver=222, episode_id=222, measure_date=date(2024, 1, 2)),
    ])
    denominator_measure = FakeMeasure(0, "full cohort", [])
    numerator_measure = FakeMeasure(30, "numerator", [
        MeasureMember(person_id=2, measure_resolver=222, episode_id=222, measure_date=date(2024, 3, 1)),
    ])
    report = build_report(
        cohort_measure=cohort_measure,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
    )
    executor = seed_executor(MeasureExecutor(None), cohort_measure, numerator_measure)

    rows = build_pivot_indicators(report, executor)

    assert len(rows) == 2
    assert [row.measure_resolver for row in rows] == [111, 222]
    assert [row.numerator_value for row in rows] == [False, True]
    assert [row.denominator_date for row in rows] == [date(2024, 1, 1), date(2024, 1, 2)]


def test_build_pivot_indicators_uses_earliest_matching_numerator_date():
    cohort_measure = FakeMeasure(10, "cohort", [
        MeasureMember(person_id=1, measure_resolver=123, episode_id=123, measure_date=date(2024, 1, 1)),
    ])
    denominator_measure = FakeMeasure(20, "denominator", [
        MeasureMember(person_id=1, measure_resolver=123, episode_id=123, measure_date=date(2024, 2, 1)),
    ])
    numerator_measure = FakeMeasure(30, "numerator", [
        MeasureMember(person_id=1, measure_resolver=123, episode_id=123, measure_date=datetime(2024, 2, 7, 14, 30)),
        MeasureMember(person_id=1, measure_resolver=123, episode_id=123, measure_date=date(2024, 2, 3)),
    ])
    report = build_report(
        cohort_measure=cohort_measure,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
    )
    executor = seed_executor(MeasureExecutor(None), cohort_measure, denominator_measure, numerator_measure)

    rows = build_pivot_indicators(report, executor)

    assert len(rows) == 1
    assert rows[0].numerator_value is True
    assert rows[0].numerator_date == date(2024, 2, 3)
    assert rows[0].denominator_date == date(2024, 2, 1)


def test_build_pivot_indicators_matches_whole_cohort_numerator_to_same_episode():
    cohort_measure = FakeMeasure(10, "cohort", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 1, 1)),
        MeasureMember(person_id=1, measure_resolver=222, episode_id=222, measure_date=date(2024, 1, 5)),
    ])
    denominator_measure = FakeMeasure(0, "full cohort", [])
    numerator_measure = FakeMeasure(30, "numerator", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 2, 7)),
    ])
    report = build_report(
        cohort_measure=cohort_measure,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
    )
    executor = seed_executor(MeasureExecutor(None), cohort_measure, numerator_measure)

    rows = build_pivot_indicators(report, executor)

    assert len(rows) == 2
    assert [row.measure_resolver for row in rows] == [111, 222]
    assert [row.numerator_value for row in rows] == [True, False]
    assert [row.numerator_date for row in rows] == [date(2024, 2, 7), None]


def test_build_pivot_indicators_keeps_each_episodes_earliest_numerator_date():
    cohort_measure = FakeMeasure(10, "cohort", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 1, 1)),
        MeasureMember(person_id=1, measure_resolver=222, episode_id=222, measure_date=date(2024, 1, 5)),
    ])
    denominator_measure = FakeMeasure(0, "full cohort", [])
    numerator_measure = FakeMeasure(30, "numerator", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 2, 7)),
        MeasureMember(person_id=1, measure_resolver=222, episode_id=222, measure_date=datetime(2024, 2, 3, 15, 30)),
    ])
    report = build_report(
        cohort_measure=cohort_measure,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
    )
    executor = seed_executor(MeasureExecutor(None), cohort_measure, numerator_measure)

    rows = build_pivot_indicators(report, executor)

    assert len(rows) == 2
    assert [row.numerator_value for row in rows] == [True, True]
    assert [row.numerator_date for row in rows] == [date(2024, 2, 7), date(2024, 2, 3)]


def test_build_pivot_indicators_excludes_undated_denominator_members():
    cohort_measure = FakeMeasure(10, "cohort", [
        MeasureMember(person_id=1, measure_resolver=101, episode_id=101, measure_date=date(2024, 1, 1)),
        MeasureMember(person_id=2, measure_resolver=202, episode_id=202, measure_date=date(2024, 1, 2)),
    ])
    denominator_measure = FakeMeasure(20, "denominator", [
        MeasureMember(person_id=1, measure_resolver=101, episode_id=101, measure_date=None),
        MeasureMember(person_id=2, measure_resolver=202, episode_id=202, measure_date=date(2024, 2, 2)),
    ])
    numerator_measure = FakeMeasure(30, "numerator", [
        MeasureMember(person_id=1, measure_resolver=101, episode_id=101, measure_date=date(2024, 2, 3)),
        MeasureMember(person_id=2, measure_resolver=202, episode_id=202, measure_date=date(2024, 2, 4)),
    ])
    report = build_report(
        cohort_measure=cohort_measure,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
    )
    executor = seed_executor(MeasureExecutor(None), cohort_measure, denominator_measure, numerator_measure)

    rows = build_pivot_indicators(report, executor)

    assert len(rows) == 1
    assert rows[0].person_id == 2
    assert rows[0].numerator_value is True
    assert rows[0].denominator_date == date(2024, 2, 2)


def test_build_pivot_indicators_keeps_nonzero_denominator_resolver_specific():
    cohort_measure = FakeMeasure(10, "cohort", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 1, 1)),
        MeasureMember(person_id=1, measure_resolver=222, episode_id=222, measure_date=date(2024, 1, 2)),
    ])
    denominator_measure = FakeMeasure(20, "denominator", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 2, 1)),
        MeasureMember(person_id=1, measure_resolver=222, episode_id=222, measure_date=date(2024, 2, 2)),
    ])
    numerator_measure = FakeMeasure(30, "numerator", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 2, 5)),
    ])
    report = build_report(
        cohort_measure=cohort_measure,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
    )
    executor = seed_executor(MeasureExecutor(None), cohort_measure, denominator_measure, numerator_measure)

    rows = build_pivot_indicators(report, executor)

    assert len(rows) == 2
    assert [row.numerator_value for row in rows] == [True, False]
    assert [row.numerator_date for row in rows] == [date(2024, 2, 5), None]


def test_build_pivot_indicators_applies_dynamic_windows_relative_to_cohort_membership_date():
    cohort_measure = FakeMeasure(10, "cohort", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 1, 1)),
    ])
    denominator_measure = FakeMeasure(20, "denominator", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 1, 8)),
    ])
    numerator_measure = FakeMeasure(30, "numerator", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 1, 25)),
    ])
    report = build_report(
        cohort_measure=cohort_measure,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
        numerator_max_days_post=20,
        denominator_max_days_post=10,
    )
    executor = seed_executor(MeasureExecutor(None), cohort_measure, denominator_measure, numerator_measure)

    rows = build_pivot_indicators(report, executor)

    assert len(rows) == 1
    assert rows[0].denominator_value is True
    assert rows[0].denominator_date == date(2024, 1, 8)
    assert rows[0].numerator_value is False
    assert rows[0].numerator_date is None


def test_build_pivot_indicators_applies_row_specific_windows_for_full_cohort_denominator():
    cohort_measure = FakeMeasure(10, "cohort", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 1, 1)),
        MeasureMember(person_id=1, measure_resolver=222, episode_id=222, measure_date=date(2024, 3, 1)),
    ])
    denominator_measure = FakeMeasure(0, "full cohort", [])
    numerator_measure = FakeMeasure(30, "numerator", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 2, 1)),
        MeasureMember(person_id=1, measure_resolver=222, episode_id=222, measure_date=date(2024, 2, 1)),
    ])
    report = build_report(
        cohort_measure=cohort_measure,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
        numerator_max_days_prior=10,
        numerator_max_days_post=40,
    )
    executor = seed_executor(MeasureExecutor(None), cohort_measure, numerator_measure)

    rows = build_pivot_indicators(report, executor)

    assert len(rows) == 2
    assert [row.measure_resolver for row in rows] == [111, 222]
    assert [row.numerator_value for row in rows] == [True, False]
    assert [row.numerator_date for row in rows] == [date(2024, 2, 1), None]


def test_build_pivot_indicators_whole_cohort_propagation_does_not_leak_across_people():
    cohort_measure = FakeMeasure(10, "cohort", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 1, 1)),
        MeasureMember(person_id=2, measure_resolver=111, episode_id=111, measure_date=date(2024, 1, 2)),
    ])
    denominator_measure = FakeMeasure(0, "full cohort", [])
    numerator_measure = FakeMeasure(30, "numerator", [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 2, 7)),
    ])
    report = build_report(
        cohort_measure=cohort_measure,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
    )
    executor = seed_executor(MeasureExecutor(None), cohort_measure, numerator_measure)

    rows = build_pivot_indicators(report, executor)

    assert len(rows) == 2
    assert [row.person_id for row in rows] == [1, 2]
    assert [row.numerator_value for row in rows] == [True, False]
    assert [row.numerator_date for row in rows] == [date(2024, 2, 7), None]


def test_report_runner_execute_allows_strict_demography_after_full_cohort_reconciliation(monkeypatch):
    cohort_members = [
        MeasureMember(person_id=1, measure_resolver=111, episode_id=111, measure_date=date(2024, 1, 1)),
        MeasureMember(person_id=2, measure_resolver=222, episode_id=222, measure_date=date(2024, 1, 2)),
    ]
    cohort_measure = FakeMeasure(10, "cohort", cohort_members)
    denominator_measure = FakeMeasure(0, "full cohort", [])
    numerator_measure = FakeMeasure(30, "numerator", [
        MeasureMember(person_id=2, measure_resolver=222, episode_id=222, measure_date=date(2024, 2, 1)),
    ])
    report = build_report(
        cohort_measure=cohort_measure,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
    )
    db = FakeDb(rows=[SimpleNamespace(person_id=1), SimpleNamespace(person_id=2)])
    runner = ReportRunner(db, report)
    captured: dict[str, list[int]] = {}

    class DummyRoot:
        def __init__(self, measure):
            self.measure = measure

        def sql_any(self):
            return None

    class DummyPlan:
        def __init__(self, root):
            self.root = root

    def fake_execute(self, measure, *, ep_override=False, people=None, force_refresh=False):
        rows = list(measure._seed_members)
        self._cache[measure.measure_id] = rows
        measure._members = rows
        return rows

    def fake_to_rows_stmt(self, *, restrict_to_person_ids=None):
        captured["person_ids"] = list(restrict_to_person_ids or [])
        return "demography_stmt"

    monkeypatch.setattr("oa_cohorts.output.report_runner.QueryPlan", DummyPlan)
    monkeypatch.setattr("oa_cohorts.output.report_runner.MeasureNode", DummyRoot)
    monkeypatch.setattr("oa_cohorts.output.report_runner.DemographyFilter.to_rows_stmt", fake_to_rows_stmt)
    monkeypatch.setattr("oa_cohorts.output.report_runner.MeasureExecutor.execute", fake_execute)

    runner.execute(strict=True)
    demography_rows = runner.collect_demography(strict=True)

    assert [row.person_id for row in demography_rows] == [1, 2]
    assert captured["person_ids"] == [1, 2]
    assert runner._executor.members(denominator_measure) == cohort_members
