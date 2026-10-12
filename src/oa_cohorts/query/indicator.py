from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

import sqlalchemy as sa
import sqlalchemy.orm as so
from orm_loader.helpers import Base

from ..core import IndicatorGrain
from ..core.executability import ExecStatus, IndicatorExecCheck
from ..core.html_utils import HTMLRenderable, RawHTML
from .indicator_members import (
    IndicatorMemberResult,
    cohort_anchor_dates,
    dedupe_members,
    earliest_date,
    indicator_grain,
    member_key,
    members_by_key,
    reduce_patient_results,
)
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
        """Eligible numerator evidence, reduced once for patient-grain indicators."""
        if indicator_grain(self) == IndicatorGrain.episode:
            return dedupe_members(self._episode_numerator_members(executor, cohort_members=cohort_members))
        return [MeasureMember(result.denominator.person_id, result.denominator.person_id,
                              measure_date=result.numerator_date)
                for result in self.patient_results(executor, cohort_members=cohort_members)
                if result.numerator_value]

    def denominator_members(self, executor: MeasureExecutor, *, cohort_members=None) -> Sequence[MeasureMember]:
        """Eligible cancer members or one eligible member per patient."""
        if indicator_grain(self) == IndicatorGrain.episode:
            return dedupe_members(self._episode_denominator_members(executor, cohort_members=cohort_members))
        return [result.denominator for result in self.patient_results(executor, cohort_members=cohort_members)]

    def patient_results(self, executor: MeasureExecutor, *, cohort_members=None) -> list[IndicatorMemberResult]:
        """Expose the reduced result with internal eligible/passing cancer keys."""
        if cohort_members is None:
            raise ValueError("Patient indicators require explicit report cohort_members scope")
        denominators = self._episode_denominator_members(executor, cohort_members=cohort_members)
        numerators_by_key = members_by_key(self._episode_numerator_members(executor, cohort_members=cohort_members))
        results = [self._episode_result(denominator, numerators_by_key.get(member_key(denominator), ()))
                   for denominator in denominators]
        return reduce_patient_results(results)

    def _episode_result(self, denominator: MeasureMember, numerators: Sequence[MeasureMember]) -> IndicatorMemberResult:
        matching = [member for member in numerators
                    if member_key(member) == member_key(denominator)
                    and (not self._uses_full_cohort_denominator()
                         or self._matches_any_cohort_anchor(member, [denominator], side="numerator"))]
        return IndicatorMemberResult(denominator, bool(matching), earliest_date([m.measure_date for m in matching]))

    def _episode_numerator_members(
        self,
        executor: MeasureExecutor,
        *,
        cohort_members: Sequence[MeasureMember] | None = None,
    ) -> Sequence[MeasureMember]:
        """Numerator evidence for eligible, cancer-aligned denominators."""
        if cohort_members is None:
            self._require_cohort_scope()
        denominator_members = self._episode_denominator_members(executor, cohort_members=cohort_members)
        if self._uses_full_cohort_denominator():
            anchors_by_key = members_by_key(denominator_members)
            return [
                member
                for member in self.numerator_measure.members(executor)
                if member.measure_date is not None
                and self._matches_any_cohort_anchor(
                    member, anchors_by_key.get(member_key(member), ()), side="numerator",
                )
            ]

        valid_keys = {member_key(member) for member in denominator_members}
        anchors = self._anchor_dates_by_key(cohort_members or ())
        return [
            member
            for member in self.numerator_measure.members(executor)
            if member_key(member) is not None and member_key(member) in valid_keys
            and self.member_within_window(
                member.measure_date,
                anchor_date=anchors.get(member_key(member)),
                side="numerator",
            )
        ]

    def _episode_denominator_members(
        self,
        executor: MeasureExecutor,
        *,
        cohort_members: Sequence[MeasureMember] | None = None,
    ) -> Sequence[MeasureMember]:
        """Eligible cohort rows or members of the executed denominator measure."""
        if self._uses_full_cohort_denominator():
            if cohort_members is None:
                self._require_cohort_scope()
            self._anchor_dates_by_key(cohort_members)
            return [
                member
                for member in cohort_members
                if self.member_within_window(
                    # For whole-cohort denominators, the cohort membership row
                    # is both the candidate member and the timing anchor.
                    member.measure_date,
                    anchor_date=self._coerce_member_date(member.measure_date),
                    side="denominator",
                )
            ]
        if cohort_members is None:
            self._require_cohort_scope()
            return [member for member in self.denominator_measure.members(executor)
                    if member.episode_id is not None and member.measure_date is not None]
        anchors = self._anchor_dates_by_key(cohort_members)
        return [
            member
            for member in self.denominator_measure.members(executor)
            if member.measure_date is not None
            and member_key(member) in anchors
            and self.member_within_window(
                member.measure_date,
                anchor_date=anchors.get(member_key(member)),
                side="denominator",
            )
        ]

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

    def _uses_full_cohort_denominator(self) -> bool:
        denominator_measure_id = getattr(self, "denominator_measure_id", None)
        if denominator_measure_id is not None:
            return denominator_measure_id == 0
        denominator_measure = getattr(self, "denominator_measure", None)
        return getattr(denominator_measure, "measure_id", None) == 0

    @staticmethod
    def _coerce_member_date(value: date | datetime | None) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        return value

    @staticmethod
    def _anchor_dates_by_key(cohort_members: Sequence[MeasureMember]):
        return cohort_anchor_dates(cohort_members)

    def _require_cohort_scope(self) -> None:
        if self._uses_full_cohort_denominator() or self.has_numerator_window() or self.has_denominator_window():
            raise ValueError("Explicit report cohort_members scope is required for whole-cohort matching or indicator windows")

    def _matches_any_cohort_anchor(
        self,
        member: MeasureMember,
        cohort_members: Sequence[MeasureMember],
        *,
        side: str,
    ) -> bool:
        """Match a complete cancer key and the eligible cohort row's own window."""
        for cohort_member in cohort_members:
            if member_key(member) is None or member_key(cohort_member) != member_key(member):
                continue
            if self.member_within_window(member.measure_date, anchor_date=cohort_member.measure_date, side=side):
                return True
        return False

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
    
