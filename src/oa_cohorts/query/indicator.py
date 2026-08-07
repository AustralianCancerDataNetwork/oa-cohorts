from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

import sqlalchemy as sa
import sqlalchemy.orm as so
from orm_loader.helpers import Base

from ..core.executability import ExecStatus, IndicatorExecCheck
from ..core.html_utils import HTMLRenderable, RawHTML
from .measure import Measure, MeasureExecutor, MeasureMember
from .report import Report, report_indicator_map


class Indicator(HTMLRenderable, Base):

    """
    Numerator / Denominator pairing representing a quality metric.

    An Indicator defines:

    - numerator_measure
    - denominator_measure
    - optional temporal constraints
    - optional indicator-level relative date windows
    - optional benchmark metadata

    Execution Model
    ----------------
    An indicator is executable iff BOTH numerator and denominator measures
    are executable.

    Indicator does not own execution logic; it delegates to MeasureExecutor.

    Dynamic date windows
    --------------------
    Numerator and denominator windows are held on the indicator rather than the
    reusable measure. They are interpreted relative to report cohort membership
    dates during payload assembly, which lets the same measure be reused in
    multiple indicators with different timing rules.
    """

    __tablename__ = 'indicator'

    indicator_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    id = so.synonym('indicator_id')

    indicator_description: so.Mapped[str] = so.mapped_column(sa.String(250))
    indicator_reference: so.Mapped[str | None] = so.mapped_column(sa.String(100), nullable=True)

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

    in_reports: so.Mapped[list[Report]] = so.relationship(
        secondary=report_indicator_map,
        back_populates="indicators"
    )

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
        """
        Execute both numerator and denominator measures for this indicator.
        """
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

    def numerator_members(
        self,
        executor: MeasureExecutor,
        *,
        cohort_members: Sequence[MeasureMember] | None = None,
    ) -> Sequence[MeasureMember]:
        """
        Members of the numerator cohort (delegates to numerator measure).

        Returns only those members who are also in the denominator cohort, as per indicator definition
        (i.e. I do not care about the numerator event for members not in the denominator).
        
        Assumes the numerator measure has been executed.
        """
        if cohort_members is None:
            num = set(self.numerator_measure.members(executor))
            den = set(self.denominator_measure.members(executor))
            return list(num & den)

        denominator_members = self.denominator_members(executor, cohort_members=cohort_members)
        if self._uses_full_cohort_denominator():
            # Whole-cohort denominators do not carry resolver-specific measure
            # rows, so numerator eligibility is anchored against each in-scope
            # cohort membership row for the same person.
            valid_people = {member.person_id for member in denominator_members}
            return [
                member
                for member in self.numerator_measure.members(executor)
                if member.person_id in valid_people and self._matches_any_cohort_anchor(member, cohort_members, side="numerator")
            ]

        valid_keys = {
            (member.person_id, member.measure_resolver)
            for member in denominator_members
        }
        anchors = self._anchor_dates_by_key(cohort_members)
        return [
            member
            for member in self.numerator_measure.members(executor)
            if (member.person_id, member.measure_resolver) in valid_keys
            and self.member_within_window(
                member.measure_date,
                anchor_date=anchors.get((member.person_id, member.measure_resolver)),
                side="numerator",
            )
        ]

    def denominator_members(
        self,
        executor: MeasureExecutor,
        *,
        cohort_members: Sequence[MeasureMember] | None = None,
    ) -> Sequence[MeasureMember]:
        """
        Members of the denominator cohort (delegates to denominator measure).
        Assumes the denominator measure has been executed.
        """
        if self._uses_full_cohort_denominator():
            if cohort_members is None:
                return self.denominator_measure.members(executor)
            members = list(cohort_members)
            return [
                member
                for member in members
                if self.member_within_window(
                    # For whole-cohort denominators, the cohort membership row
                    # is both the candidate member and the timing anchor.
                    member.measure_date,
                    anchor_date=self._coerce_member_date(member.measure_date),
                    side="denominator",
                )
            ]
        if cohort_members is None:
            return self.denominator_measure.members(executor)
        anchors = self._anchor_dates_by_key(cohort_members)
        return [
            member
            for member in self.denominator_measure.members(executor)
            if member.measure_date is not None
            and (member.person_id, member.measure_resolver) in anchors
            and self.member_within_window(
                member.measure_date,
                anchor_date=anchors.get((member.person_id, member.measure_resolver)),
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

    def _anchor_dates_by_key(self, cohort_members: Sequence[MeasureMember]) -> dict[tuple[int, int], date | None]:
        """Resolve the earliest cohort membership date for each person/resolver pair."""
        anchors: dict[tuple[int, int], date | None] = {}
        for member in cohort_members:
            key = (member.person_id, member.measure_resolver)
            candidate = self._coerce_member_date(member.measure_date)
            current = anchors.get(key)
            if current is None:
                anchors[key] = candidate
            elif candidate is not None:
                anchors[key] = min(current, candidate)
        return anchors

    def _matches_any_cohort_anchor(
        self,
        member: MeasureMember,
        cohort_members: Sequence[MeasureMember],
        *,
        side: str,
    ) -> bool:
        """Check whether a member satisfies the dated window for any cohort row for that person."""
        for cohort_member in cohort_members:
            if cohort_member.person_id != member.person_id:
                continue
            if self.member_within_window(member.measure_date, anchor_date=cohort_member.measure_date, side=side):
                return True
        return False

    def __lt__(self, other):
        if self.indicator_id != other.indicator_id:
            return self.indicator_id < other.indicator_id
        return self.indicator_description < other.indicator_description

    def __repr__(self):
        i = f'({self.id}) {self.indicator_description}'
        if self.indicator_reference:
            i += f' [{self.indicator_reference}]'
        n = f'\tNumerator: {self.numerator_measure}'
        d = f'\tDenominator: {self.denominator_measure}'
        return f'{i}\n{n}\n{d}'

    def _html_css_class(self) -> str:
        return "indicator"

    def _html_title(self) -> str:
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
    
