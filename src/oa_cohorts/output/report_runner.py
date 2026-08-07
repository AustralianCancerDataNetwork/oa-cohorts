from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import sqlalchemy.orm as so

from ..query.measure import MeasureExecutor, MeasureMember
from ..query.report import Report
from .person_demography import DemographyFilter
from .pivot_queries import (
    build_cohort_demography,
    build_pivot_cohort,
    build_pivot_indicators,
    build_report_payload,
    collect_report_cohort_members,
)
from .query_plan import MeasureNode, QueryPlan
from .report_payload import PivotCohortRow, PivotIndicatorRow, ReportBundle

if TYPE_CHECKING:
    from omop_constructs.alchemy.demography import PersonDemography

class ReportRunner:
    """
    Orchestrates execution and materialisation of a Report into a transport payload.
    """

    def __init__(self, db: so.Session, report: Report):
        self.db = db
        self.report = report
        self._demography_rows: Sequence[PersonDemography] | None = None
        self._cohort_rows: list[PivotCohortRow] | None = None
        self._indicator_rows: list[PivotIndicatorRow] | None = None
        self._report_cohort_members: list[MeasureMember] | None = None
        self._plans: dict[int, QueryPlan] = {}
        self._executor = MeasureExecutor(db)

    def execute(self, *, people: list[int] | None = None, strict: bool = True) -> None:
        """
        Executes all measures needed for this report.
        """
        self._report_cohort_members = None
        self._demography_rows = None
        self._cohort_rows = None
        self._indicator_rows = None

        # 1. Preflight compile
        for m in self.report.indicator_measures + self.report.cohort_measures:
            try:
                plan = QueryPlan(root=MeasureNode(m))
                _ = plan.root.sql_any()
            except Exception as e:
                if strict:
                    raise RuntimeError(f"Preflight compilation failed for measure {m.measure_id} ({m.name}): {e}")
                else:
                    print(f"Warning: Preflight compilation failed for measure {m.measure_id} ({m.name}): {e}")
        # 2. Execute actual measures
        self.report.execute(self._executor, people=people, strict=strict)

    def build_plans(self) -> dict[int, QueryPlan]:
        if not self._plans:
            for m in self.report.indicator_measures + self.report.cohort_measures:
                self._plans[m.measure_id] = QueryPlan(root=MeasureNode(m))
        return self._plans

    def all_plan_measures(self) -> set[int]:
        ids = set()
        for m in self.report.indicator_measures + self.report.cohort_measures:
            plan = QueryPlan(root=MeasureNode(m))
            for mm in plan.root.iter_measures():
                ids.add(mm.measure_id)
        return ids

    def collect_demography(self, strict: bool = True) -> Sequence[PersonDemography]:
        # we can tolerate some indicator execution failures but we do not support
        # cohorts with failed execution, so strict only covers assertion step here
        if strict:
            self.report.assert_executed()

        cohort_person_ids = [m.person_id for m in self.collect_report_cohort_members()]

        demo_filter = DemographyFilter()

        stmt = demo_filter.to_rows_stmt(
            restrict_to_person_ids=cohort_person_ids
        )

        self._demography_rows = self.db.execute(stmt).scalars().all()
        return self._demography_rows

    def collect_report_cohort_members(self) -> list[MeasureMember]:
        if self._report_cohort_members is None:
            self._report_cohort_members = collect_report_cohort_members(self.report, self._executor)
        return self._report_cohort_members

    def collect_pivot_cohort(self, strict: bool = True) -> list[PivotCohortRow]:
        """
        Build cohort × measure pivot rows from executed Measure.members.
        """
        self._cohort_rows = build_pivot_cohort(self.report, executor=self._executor, strict=strict)
        return self._cohort_rows

    def collect_pivot_indicators(self, strict: bool = True) -> list[PivotIndicatorRow]:
        """
        Build indicator pivot rows from executed Measure.members.
        """
        self._indicator_rows = build_pivot_indicators(
            self.report,
            executor=self._executor,
            strict=strict,
            cohort_members=self.collect_report_cohort_members(),
        )
        return self._indicator_rows
    
    def build_bundle(self, strict: bool = True) -> ReportBundle:
        if self._demography_rows is None:
            self.collect_demography(strict=strict)

        if self._cohort_rows is None:
            self.collect_pivot_cohort(strict=strict)

        if self._indicator_rows is None:
            self.collect_pivot_indicators(strict=strict)

        assert self._demography_rows is not None
        assert self._cohort_rows is not None
        assert self._indicator_rows is not None

        return ReportBundle(
            report=build_report_payload(self.report, self._executor),
            cohort_demography=build_cohort_demography(self._demography_rows),
            pivot_cohort=self._cohort_rows,
            pivot_indicators=self._indicator_rows,
        )

    def to_json(self) -> dict:
        bundle = self.build_bundle()
        return bundle.model_dump(mode="json")
