from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from itertools import chain
from typing import TYPE_CHECKING

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import get_logger
from orm_loader.helpers import Base
from sqlalchemy.ext.associationproxy import association_proxy

from oa_cohorts.query.measure import MeasureExecutor, MeasureMember

from ..core.executability import ExecStatus
from ..core.html_utils import HTMLRenderable, RawHTML, esc, exec_badge, table, td
from .typing import PersonFilter

if TYPE_CHECKING:
    from .dash_cohort import DashCohort
    from .indicator import Indicator

logger = get_logger(__name__)

report_indicator_map = sa.Table(
    'report_indicator_map',
    Base.metadata,
    sa.Column('report_id', sa.ForeignKey('report.report_id'), primary_key=True),
    sa.Column('indicator_id', sa.ForeignKey('indicator.indicator_id'), primary_key=True),
)

class ReportCohortMap(HTMLRenderable, Base):
    """
    Maps cohorts to reports, with primary/non-primary semantics.
    """
    __tablename__ = 'report_cohort_map'

    report_cohort_map_id: so.Mapped[int] = so.mapped_column(sa.Integer, primary_key=True)
    id = so.synonym('report_cohort_map_id')

    report_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('report.report_id'))
    dash_cohort_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('dash_cohort.dash_cohort_id'))
    primary_cohort: so.Mapped[bool] = so.mapped_column(sa.Boolean, default=False)

    cohort: so.Mapped[DashCohort] = so.relationship(back_populates='in_reports', lazy="joined")
    report: so.Mapped[Report] = so.relationship(back_populates='cohorts')

    measures = association_proxy("cohort", "measures")
    definition_count = association_proxy("cohort", "definition_count")

    @property
    def measure_count(self) -> int:
        if self.cohort:
            return sum(d.measure_count for d in self.cohort.definitions)
        return 0

    def __repr__(self):
        if self.cohort:
            return f"<ReportCohortMap cohort={self.cohort.dash_cohort_name!r} primary={self.primary_cohort}>"
        return super().__repr__()

    def _html_css_class(self) -> str:
        return "report-cohort"

    def _html_title(self) -> str:
        label = "Primary cohort" if self.primary_cohort else "Cohort"
        return f"{label}: {self.cohort.dash_cohort_name if self.cohort else self.dash_cohort_id}"

    def _html_header(self) -> dict[str, str]:
        hdr = {
            "Primary": "yes" if self.primary_cohort else "no",
        }

        if self.cohort:
            hdr["Cohort"] = self.cohort.dash_cohort_name
            hdr["Definitions"] = str(self.cohort.definition_count)
            hdr["Measures"] = str(self.measure_count)

        return hdr

    def _html_inner(self):
        blocks: list[object] = []

        if not self.cohort:
            blocks.append(RawHTML("<div class='muted'><i>Cohort not loaded</i></div>"))
            return blocks

        # Delegate to Dash_Cohort renderer
        blocks.append(self.cohort)

        return blocks
    
class Report(HTMLRenderable, Base):
    """
    Top-level reporting unit.

    A Report defines:

    - One or more DashCohorts (population definitions)
    - One or more Indicators (numerator/denominator logic)

    Responsibilities
    ----------------
    - Coordinates execution of all required measures.
    - Defines execution boundary for "measure_id = 0" (full cohort).
    - Aggregates executable status across cohorts and indicators.
    - Provides consolidated access to report-level measures and members.

    Reports do NOT generate SQL directly.
    They orchestrate MeasureExecutor usage.
    """

    __tablename__ = 'report'

    report_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    id = so.synonym('report_id')

    report_name: so.Mapped[str] = so.mapped_column(sa.String(250))
    report_short_name: so.Mapped[str] = so.mapped_column(sa.String(50), unique=True)
    report_description: so.Mapped[str] = so.mapped_column(sa.String(1000))
    report_create_date: so.Mapped[date] = so.mapped_column(sa.Date, default=date.today)
    report_edit_date: so.Mapped[date] = so.mapped_column(sa.Date, default=date.today)
    report_author: so.Mapped[str] = so.mapped_column(sa.String(250))
    report_owner: so.Mapped[str | None] = so.mapped_column(sa.String(250), nullable=True)

    cohorts: so.Mapped[list[ReportCohortMap]] = so.relationship(back_populates='report')
    indicators: so.Mapped[list[Indicator]] = so.relationship(
        secondary=report_indicator_map,
        back_populates="in_reports",
        lazy="selectin",
    )

    denominator_measures = association_proxy("indicators", "denominator_measure")
    numerator_measures = association_proxy("indicators", "numerator_measure")

    @property
    def report_cohorts(self):
        return [c.cohort for c in self.cohorts]

    @property
    def indicator_measures(self):
        return list(set(self.numerator_measures + self.denominator_measures))

    @property
    def cohort_measures(self):
        return list(set(chain.from_iterable([c.measures for c in self.report_cohorts])))
    
    def members(self, executor: MeasureExecutor) -> Sequence[MeasureMember]:
        return list(set(chain.from_iterable([c.members(executor) for c in self.report_cohorts])))

    def execute(
            self, 
            executor: MeasureExecutor,
            *, 
            people: list[int] | None = None, 
            person_filter: PersonFilter | None = None,
            strict: bool = True
        ) -> None:

        """
        Execute all measures required by this report.

        Execution Order
        ---------------
        1. All report-level measures (numerator, denominator, cohort measures)
        2. Full-cohort (measure_id = 0) members resolved at report level

        Parameters
        ----------
        db:
            Active SQLAlchemy session.
        people:
            Optional list of person_ids to restrict execution.
        person_filter:
            Reserved for future filtering strategies.
        strict:
            If True, raise on first failure.
            If False, log and continue.
        """
        #from .measure import MeasureExecutor
        #executor = MeasureExecutor(db)

        for m in self.indicator_measures + self.cohort_measures:
            if m.measure_id == 0:
                continue
            try:
                logger.info(f"Executing measure {m.name} (ID {m.measure_id}) for report {self.report_short_name}")
                executor.execute(m, people=people)
            except Exception as e:
                logger.error(f"Error executing measure {m.name} (ID {m.measure_id}): {e}")
                executor.db.rollback()
                if strict:
                    raise

        cohort_members = self.members(executor)
        for m in self.indicator_measures:
            if m.measure_id == 0:
                executor._cache[m.measure_id] = cohort_members
                m._members = cohort_members

    def assert_executed(self):
        for m in self.indicator_measures + self.cohort_measures:
            if m.measure_id != 0 and m._members is None:
                raise RuntimeError(f"Measure {m.measure_id} not executed")

    def __repr__(self):
        return f"<Report {self.report_short_name!r} ({len(self.indicators)} indicators)>"

    def _html_css_class(self) -> str:
        return "report"

    def _html_title(self) -> str:
        return f"Report: {self.report_name}"

    def _html_header(self) -> dict[str, str]:
        hdr = {
            "Short name": self.report_short_name,
            "Author": self.report_author,
        }

        if self.report_owner:
            hdr["Owner"] = self.report_owner

        return hdr

    def _html_inner(self):
        blocks: list[object] = []

        # Description
        if self.report_description:
            blocks.append(RawHTML(f"<div class='muted'>{esc(self.report_description)}</div>"))
        
        blocks.extend(self._html_exec_summary())
        
        # Indicators
        blocks.append(RawHTML("<div class='subquery-section-title'>Indicators</div>"))

        if self.indicators:
            for ind in sorted(self.indicators):
                blocks.append(
                    RawHTML("<details class='indicator-collapse'>")
                )
                blocks.append(
                    RawHTML(
                        f"<summary><b>{esc(ind.indicator_description)}</b></summary>"
                    )
                )
                blocks.append(ind)
                blocks.append(RawHTML("</details>"))
        else:
            blocks.append(RawHTML("<div class='muted'><i>No indicators</i></div>"))

        return blocks
    
    def executable_status(self) -> ExecStatus:
        statuses: list[ExecStatus] = []

        # Indicator-level statuses
        for ind in self.indicators:
            statuses.append(ind.is_executable().status)

        # Dash cohort definition statuses
        for rc in self.cohorts:
            cohort = rc.cohort
            if not cohort:
                continue
            for d in cohort.definitions:
                statuses.append(d.is_executable().status)

        if ExecStatus.FAIL in statuses:
            return ExecStatus.FAIL
        if ExecStatus.WARN in statuses:
            return ExecStatus.WARN
        return ExecStatus.PASS
    

    def _html_exec_summary(self):
        blocks: list[object] = []

        # === Overall header ===
        overall = self.executable_status()
        blocks.append(RawHTML("<div class='subquery-section-title'>Executability Summary</div>"))
        blocks.append(
            RawHTML(
                f"<div style='margin-bottom:8px'>"
                f"<b>Overall report executability:</b> {exec_badge(overall)}</div>"
            )
        )

        # === Section 1: Dash cohorts ===
        headers = [
            "Cohort",
            "Definition",
            "Measure",
            "Status",
        ]

        cohort_rows = []

        for rc in self.cohorts:
            cohort = rc.cohort
            if not cohort:
                cohort_rows.append([
                    td("<i>Missing cohort</i>"), td(""), td(""), td(exec_badge(ExecStatus.FAIL))
                ])
                continue

            for d in cohort.definitions:
                check = d.is_executable()
                cohort_rows.append([
                    td(cohort.dash_cohort_name),
                    td(d.dash_cohort_def_name),
                    td(d.dash_cohort_measure.name if d.dash_cohort_measure else "<i>None</i>"),
                    td(exec_badge(check.status)),
                ])

        if len(cohort_rows) > 1:
            blocks.append(
                RawHTML(
                    table(
                        headers=headers,
                        rows=cohort_rows,
                        cls="concept-table compact"
                    )
                )
            )
        else:
            blocks.append(RawHTML("<div class='muted'><i>No dash cohorts</i></div>"))

        # === Section 2: Indicators ===
        blocks.append(RawHTML("<div class='subquery-section-title'>Indicators</div>"))

        headers = [
            "Indicator",
            "Numerator",
            "Num",
            "Denominator",
            "Den",
            "Indicator Status",
        ]

        indicator_rows = []

        for ind in sorted(self.indicators):
            check = ind.is_executable()

            indicator_rows.append([
                td(ind.indicator_description),
                td(f'{ind.numerator_measure.id} - {ind.numerator_measure.name}'),
                td(exec_badge(check.numerator.status)),
                td(f'{ind.denominator_measure.id} - {ind.denominator_measure.name}' if ind.denominator_measure else "<i>Full cohort</i>"),
                td(exec_badge(check.denominator.status)),
                td(exec_badge(check.status)),
            ])

        if len(indicator_rows) > 1:
            blocks.append(
                RawHTML(
                    table(
                        headers=headers,
                        rows=indicator_rows,
                        cls="concept-table compact"
                    )
                )
            )
        else:
            blocks.append(RawHTML("<div class='muted'><i>No indicators</i></div>"))

        return blocks
