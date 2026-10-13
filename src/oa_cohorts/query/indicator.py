from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

import sqlalchemy as sa
import sqlalchemy.orm as so
from orm_loader.helpers import Base

from ..core import IndicatorGrain
from ..core.executability import ExecStatus, IndicatorExecCheck
from ..core.html_utils import HTMLRenderable, RawHTML
from .indicator_evaluation import evaluate_indicator, outcome_members
from .indicator_members import IndicatorMemberResult
from .measure import Measure, MeasureExecutor, MeasureMember
from .report import Report, ReportIndicatorMap


class Indicator(HTMLRenderable, Base):
    """Quality metric pairing numerator and denominator measures.

    Windows use report cohort dates. Episode results match the complete cancer
    member key; patient results reduce eligible episode results once.
    """

    __tablename__ = 'indicator'
    __table_args__ = (
        sa.CheckConstraint("grain IN ('episode', 'patient')", name="indicator_grain"),
        sa.CheckConstraint("opportunity_selection IN ('any', 'first')", name="indicator_opportunity_selection"),
        sa.CheckConstraint("numerator_reference IN ('cohort', 'denominator')", name="indicator_numerator_reference"),
        sa.CheckConstraint("period_date_source IN ('cohort', 'denominator', 'numerator', 'completion')", name="indicator_period_date_source"),
        sa.CheckConstraint("undated_numerator_policy IN ('exclude', 'presence')", name="indicator_undated_numerator_policy"),
    )

    indicator_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    id = so.synonym('indicator_id')

    indicator_description: so.Mapped[str] = so.mapped_column(sa.String(250))
    indicator_reference: so.Mapped[str | None] = so.mapped_column(sa.String(100), nullable=True)

    grain: so.Mapped[IndicatorGrain] = so.mapped_column(
        sa.Enum(IndicatorGrain, native_enum=False, create_constraint=False,
                validate_strings=True, name="indicator_grain"),
        default=IndicatorGrain.episode, server_default="episode", nullable=False,
    )

    opportunity_selection: so.Mapped[str] = so.mapped_column(sa.String(5), default="any", server_default="any", nullable=False)
    numerator_reference: so.Mapped[str] = so.mapped_column(sa.String(11), default="cohort", server_default="cohort", nullable=False)
    period_date_source: so.Mapped[str] = so.mapped_column(sa.String(11), default="denominator", server_default="denominator", nullable=False)
    undated_numerator_policy: so.Mapped[str] = so.mapped_column(sa.String(8), default="exclude", server_default="exclude", nullable=False)

    @so.validates("grain")
    def _validate_grain(self, key: str, value) -> IndicatorGrain:
        return IndicatorGrain(value)

    numerator_measure_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('measure.measure_id'))
    denominator_measure_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('measure.measure_id'))

    numerator_max_days_prior: so.Mapped[int | None] = so.mapped_column(sa.Integer(), nullable=True)
    numerator_max_days_post: so.Mapped[int | None] = so.mapped_column(sa.Integer(), nullable=True)

    denominator_max_days_prior: so.Mapped[int | None] = so.mapped_column(sa.Integer(), nullable=True)
    denominator_max_days_post: so.Mapped[int | None] = so.mapped_column(sa.Integer(), nullable=True)

    benchmark: so.Mapped[int | None] = so.mapped_column(sa.Integer(), nullable=True)
    benchmark_unit: so.Mapped[str | None] = so.mapped_column(sa.String(20), nullable=True)

    numerator_measure: so.Mapped[Measure] = so.relationship(
        foreign_keys=[numerator_measure_id], lazy="joined"
    )
    denominator_measure: so.Mapped[Measure] = so.relationship(
        foreign_keys=[denominator_measure_id], lazy="joined"
    )

    report_links: so.Mapped[list[ReportIndicatorMap]] = so.relationship(
        back_populates='indicator'
    )
    # viewonly for the same reason as Report.indicators: see the note there.
    in_reports: so.Mapped[list[Report]] = so.relationship(
        secondary=ReportIndicatorMap.__table__,
        back_populates="indicators",
        viewonly=True,
    )

    def link_for(self, report: Report) -> ReportIndicatorMap | None:
        """Return this indicator's link to ``report``, or ``None`` if it is not in it."""
        report_id = getattr(report, 'report_id', report)
        for link in self.report_links:
            if link.report_id == report_id:
                return link
        return None

    def label_in(self, report: Report) -> str:
        """The description as ``report`` states it, falling back to the canonical value.
        """
        link = self.link_for(report)
        if link is not None:
            return link.label
        return self.indicator_description

    def reference_in(self, report: Report) -> str | None:
        """The citation as ``report`` states it, falling back to the canonical value."""
        link = self.link_for(report)
        if link is not None:
            return link.reference
        return self.indicator_reference

    def benchmark_in(self, report: Report) -> tuple[int | None, str | None]:
        """The benchmark and its unit as ``report`` states them."""
        link = self.link_for(report)
        if link is not None:
            return link.benchmark, link.benchmark_unit
        return self.benchmark, self.benchmark_unit

    @property
    def numerator_label(self) -> str:
        if self.numerator_measure:
            return self.numerator_measure.name
        return 'no numerator measure defined'

    @property
    def denominator_label(self) -> str:
        if self.denominator_measure:
            return self.denominator_measure.name
        if self.denominator_measure_id == 0:
            return 'Whole cohort'
        return 'no denominator measure defined'

    def execute(self, db: so.Session, *, people: list[int] | None = None):
        """Execute both numerator and denominator measures for this indicator."""
        from .measure import MeasureExecutor

        executor = MeasureExecutor(db)

        executor.execute(
            self.denominator_measure,
            people=people,
        )

        executor.execute(
            self.numerator_measure,
            people=people,
        )

    def numerator_members(self, executor: MeasureExecutor, *, cohort_members=None) -> Sequence[MeasureMember]:
        """One passing member per configured counting unit, using the report evaluator."""
        return outcome_members(evaluate_indicator(self, executor, cohort_members), numerator=True)

    def denominator_members(self, executor: MeasureExecutor, *, cohort_members=None) -> Sequence[MeasureMember]:
        """One eligible member per counting unit; unresolved eligibility is separate."""
        return outcome_members(evaluate_indicator(self, executor, cohort_members), numerator=False)

    def patient_results(self, executor: MeasureExecutor, *, cohort_members=None) -> list[IndicatorMemberResult]:
        """Reduced results with their eligible, passing and unresolved cancer keys."""
        if cohort_members is None:
            raise ValueError("Patient indicators require explicit report cohort_members scope")
        return evaluate_indicator(self, executor, cohort_members, grain=IndicatorGrain.patient)

    def numerator_window(self) -> tuple[int | None, int | None]:
        """Return the configured numerator window as (prior_days, post_days)."""
        return (
            getattr(self, "numerator_max_days_prior", None),
            getattr(self, "numerator_max_days_post", None),
        )

    def denominator_window(self) -> tuple[int | None, int | None]:
        """Return the configured denominator window as (prior_days, post_days)."""
        return (
            getattr(self, "denominator_max_days_prior", None),
            getattr(self, "denominator_max_days_post", None),
        )

    def has_numerator_window(self) -> bool:
        """Return whether a numerator date window is configured."""
        prior, post = self.numerator_window()
        return prior is not None or post is not None

    def has_denominator_window(self) -> bool:
        """Return whether a denominator date window is configured."""
        prior, post = self.denominator_window()
        return prior is not None or post is not None

    def member_within_window(
        self,
        member_date: date | datetime | None,
        *,
        anchor_date: date | datetime | None,
        side: str,
    ) -> bool:
        """Check whether a member date falls inside the configured relative window."""
        prior_days, post_days = self._window_for_side(side)
        if prior_days is None and post_days is None:
            return True

        resolved_member_date = self._coerce_member_date(member_date)
        resolved_anchor_date = self._coerce_member_date(anchor_date)
        # Once a dynamic window is configured, both dates must be present.
        if resolved_member_date is None or resolved_anchor_date is None:
            return False
        before_window = (
            prior_days is not None
            and resolved_member_date < resolved_anchor_date - timedelta(days=prior_days)
        )
        after_window = (
            post_days is not None
            and resolved_member_date > resolved_anchor_date + timedelta(days=post_days)
        )
        return not (before_window or after_window)

    def _window_for_side(self, side: str) -> tuple[int | None, int | None]:
        if side == "numerator":
            return self.numerator_window()
        if side == "denominator":
            return self.denominator_window()
        raise ValueError(f"Unknown indicator side: {side}")

    @staticmethod
    def _coerce_member_date(value: date | datetime | None) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        return value

    def __lt__(self, other):
        if self.indicator_id != other.indicator_id:
            return self.indicator_id < other.indicator_id
        return self.indicator_description < other.indicator_description

    def __repr__(self):
        # Canonical values; see _html_title.
        i = f'({self.id}) {self.indicator_description}'
        if self.indicator_reference:
            i += f' [{self.indicator_reference}]'
        n = f'\tNumerator: {self.numerator_measure}'
        d = f'\tDenominator: {self.denominator_measure}'
        return f'{i}\n{n}\n{d}'

    def _html_css_class(self) -> str:
        return "indicator"

    def _html_title(self) -> str:
        # Canonical: an indicator rendered on its own has no report to resolve against.
        # Any report that states it differently shows up in the header.
        return f"Indicator: {self.indicator_description}"

    def _html_header(self) -> dict[str, str]:
        hdr: dict[str, str] = {
            "ID": str(self.indicator_id),
        }

        if self.indicator_reference:
            hdr["Reference"] = self.indicator_reference

        if self.benchmark is not None:
            unit = self.benchmark_unit or ""
            hdr["Benchmark"] = f"{self.benchmark} {unit}".strip()

        if self.has_numerator_window():
            prior, post = self.numerator_window()
            hdr["Numerator window"] = f"-{prior if prior is not None else '∞'} / +{post if post is not None else '∞'} days"
        if self.has_denominator_window():
            prior, post = self.denominator_window()
            hdr["Denominator window"] = f"-{prior if prior is not None else '∞'} / +{post if post is not None else '∞'} days"

        restated = [
            f"{link.report.report_short_name}: {link.label}"
            for link in self.report_links
            if link.report is not None and link.has_overrides
        ]
        if restated:
            hdr["Restated by"] = "; ".join(sorted(restated))

        return hdr

    def _html_inner(self):
        blocks: list[object] = []
        blocks.append(RawHTML("<div class='subquery-section-title'>Numerator</div>"))
        blocks.append(self.numerator_measure)
        blocks.append(RawHTML("<div class='subquery-section-title'>Denominator</div>"))
        blocks.append(self.denominator_measure)
        return blocks
    
    def is_executable(self) -> IndicatorExecCheck:
        """
        Indicator is executable iff BOTH numerator and denominator are executable.

        PASS = both PASS
        WARN = at least one WARN, none FAIL
        FAIL = either FAIL
        """
        num_check = self.numerator_measure.is_executable()
        den_check = self.denominator_measure.is_executable()

        statuses = {num_check.status, den_check.status}

        if ExecStatus.FAIL in statuses:
            status = ExecStatus.FAIL
        elif ExecStatus.WARN in statuses:
            status = ExecStatus.WARN
        else:
            status = ExecStatus.PASS

        return IndicatorExecCheck(
            status=status,
            numerator=num_check,
            denominator=den_check,
        )
    
