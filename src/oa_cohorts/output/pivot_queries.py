from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from ..query.measure import MeasureExecutor, MeasureMember
from ..query.report import Report
from .report_payload import (
    CohortDemographyRow,
    DashCohortDefinitionPayload,
    DashCohortPayload,
    IndicatorPayload,
    MeasureSummary,
    PivotCohortRow,
    PivotIndicatorRow,
    ReportMeasurePayload,
    ReportPayload,
)

if TYPE_CHECKING:
    from omop_constructs.alchemy.demography import PersonDemography


def collect_report_cohort_members(report: Report, executor: MeasureExecutor) -> list[MeasureMember]:
    members: list[MeasureMember] = []

    for report_cohort in report.cohorts:
        cohort = report_cohort.cohort
        if not cohort:
            continue

        for definition in cohort.definitions:
            measure = definition.dash_cohort_measure
            if not measure:
                continue
            members.extend(measure.members(executor))

    return members


def _dedupe_members(members: Sequence[MeasureMember]) -> list[MeasureMember]:
    seen: set[MeasureMember] = set()
    out: list[MeasureMember] = []

    for member in members:
        if member in seen:
            continue
        seen.add(member)
        out.append(member)

    return out


def _coerce_date(value: date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


def _pick_earliest_date(current: date | None, candidate: date | None) -> date | None:
    if current is None:
        return candidate
    if candidate is None:
        return current
    return min(current, candidate)


def _build_person_resolved_numerator_dates(
    members: Sequence[MeasureMember],
    *,
    valid_people: set[int],
) -> dict[int, date | None]:
    numerator_dates: dict[int, date | None] = {}

    for member in members:
        if member.person_id not in valid_people:
            continue
        numerator_dates[member.person_id] = _pick_earliest_date(
            numerator_dates.get(member.person_id),
            _coerce_date(member.measure_date),
        )

    return numerator_dates


def _build_resolver_resolved_numerator_dates(
    members: Sequence[MeasureMember],
    *,
    valid_keys: set[tuple[int, int]],
) -> dict[tuple[int, int], date | None]:
    numerator_dates: dict[tuple[int, int], date | None] = {}

    for member in members:
        key = (member.person_id, member.measure_resolver)
        if key not in valid_keys:
            continue
        numerator_dates[key] = _pick_earliest_date(
            numerator_dates.get(key),
            _coerce_date(member.measure_date),
        )

    return numerator_dates


def _indicator_window(indicator, side: str) -> tuple[int | None, int | None]:
    return (
        getattr(indicator, f"{side}_max_days_prior", None),
        getattr(indicator, f"{side}_max_days_post", None),
    )


def _member_within_indicator_window(
    indicator,
    *,
    side: str,
    member_date: date | datetime | None,
    anchor_date: date | datetime | None,
) -> bool:
    """Apply indicator-level relative windows against a cohort-membership anchor."""
    if hasattr(indicator, "member_within_window"):
        return indicator.member_within_window(member_date, anchor_date=anchor_date, side=side)
    prior_days, post_days = _indicator_window(indicator, side)
    if prior_days is None and post_days is None:
        return True
    resolved_member_date = _coerce_date(member_date)
    resolved_anchor_date = _coerce_date(anchor_date)
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


def _cohort_anchor_dates_by_key(
    cohort_members: Sequence[MeasureMember],
) -> dict[tuple[int, int], date | None]:
    """Use the earliest in-scope cohort membership date as the resolver anchor."""
    anchors: dict[tuple[int, int], date | None] = {}
    for member in cohort_members:
        key = (member.person_id, member.measure_resolver)
        candidate = _coerce_date(member.measure_date)
        current = anchors.get(key)
        if current is None:
            anchors[key] = candidate
        elif candidate is not None:
            anchors[key] = min(current, candidate)
    return anchors


def _earliest_numerator_date_for_person_anchor(
    indicator,
    members: Sequence[MeasureMember],
    *,
    person_id: int,
    anchor_date: date | datetime | None,
) -> date | None:
    """Find the earliest numerator date for a person that satisfies the anchor window."""
    earliest: date | None = None
    for member in members:
        if member.person_id != person_id:
            continue
        if not _member_within_indicator_window(
            indicator,
            side="numerator",
            member_date=member.measure_date,
            anchor_date=anchor_date,
        ):
            continue
        earliest = _pick_earliest_date(earliest, _coerce_date(member.measure_date))
    return earliest


def _build_windowed_resolver_numerator_dates(
    indicator,
    members: Sequence[MeasureMember],
    *,
    anchor_dates_by_key: dict[tuple[int, int], date | None],
    valid_keys: set[tuple[int, int]],
) -> dict[tuple[int, int], date | None]:
    """Build earliest numerator dates after applying resolver-specific cohort anchors."""
    numerator_dates: dict[tuple[int, int], date | None] = {}
    for member in members:
        key = (member.person_id, member.measure_resolver)
        if key not in valid_keys:
            continue
        if not _member_within_indicator_window(
            indicator,
            side="numerator",
            member_date=member.measure_date,
            anchor_date=anchor_dates_by_key.get(key),
        ):
            continue
        numerator_dates[key] = _pick_earliest_date(
            numerator_dates.get(key),
            _coerce_date(member.measure_date),
        )
    return numerator_dates


def build_cohort_demography(rows: Sequence[PersonDemography]) -> list[CohortDemographyRow]:
    out: list[CohortDemographyRow] = []
    for r in rows:
        out.append(
            CohortDemographyRow(
                person_id=r.person_id,
                mrn=getattr(r, "mrn", None),
                year_of_birth=getattr(r, "year_of_birth", None),
                death_datetime=getattr(r, "death_datetime", None),
                gender=getattr(r, "sex", None) or getattr(r, "gender", None),
                language_spoken=getattr(r, "language_spoken", None),
                country_of_birth=getattr(r, "country_of_birth", None),
                post_code=getattr(r, "post_code", None),
            )
        )

    return out


def build_pivot_indicators(
    report: Report,
    executor: MeasureExecutor,
    strict: bool = True,
    cohort_members: Sequence[MeasureMember] | None = None,
) -> list[PivotIndicatorRow]:
    rows: list[PivotIndicatorRow] = []
    resolved_cohort_members = list(cohort_members) if cohort_members is not None else collect_report_cohort_members(report, executor)
    # Dynamic indicator windows are evaluated relative to report cohort
    # membership dates, not embedded into reusable measure execution.
    cohort_anchor_dates = _cohort_anchor_dates_by_key(resolved_cohort_members)

    for ind in report.indicators:
        try:
            if ind.denominator_measure_id == 0:
                # Whole-cohort denominators use the cohort membership rows
                # directly, while still respecting any configured denominator
                # window for each individual in-scope cohort row.
                denominator_members = _dedupe_members(
                    [
                        member
                        for member in resolved_cohort_members
                        if _member_within_indicator_window(
                            ind,
                            side="denominator",
                            member_date=member.measure_date,
                            anchor_date=member.measure_date,
                        )
                    ]
                )
                numerator_members = ind.numerator_measure.members(executor)
            else:
                # Explicit denominator measures remain resolver-specific, but
                # their dated eligibility is still anchored to report cohort
                # membership rather than to the denominator event itself.
                denominator_members = _dedupe_members([
                    mm
                    for mm in ind.denominator_measure.members(executor)
                    if mm.measure_date is not None
                    and (mm.person_id, mm.measure_resolver) in cohort_anchor_dates
                    and _member_within_indicator_window(
                        ind,
                        side="denominator",
                        member_date=mm.measure_date,
                        anchor_date=cohort_anchor_dates.get((mm.person_id, mm.measure_resolver)),
                    )
                ])
                numerator_dates_by_key = _build_windowed_resolver_numerator_dates(
                    ind,
                    ind.numerator_measure.members(executor),
                    anchor_dates_by_key=cohort_anchor_dates,
                    valid_keys={(member.person_id, member.measure_resolver) for member in denominator_members},
                )

            for mm in denominator_members:
                # Whole-cohort denominators should not emit both "pass" and "fail"
                # rows for the same person when a numerator event is linked to only
                # one of several in-scope episodes. With dynamic windows turned
                # on, each row still evaluates against its own cohort anchor
                # date, so different episodes for the same person can diverge.
                if ind.denominator_measure_id == 0:
                    numerator_date = _earliest_numerator_date_for_person_anchor(
                        ind,
                        numerator_members,
                        person_id=mm.person_id,
                        anchor_date=mm.measure_date,
                    )
                    numerator_value = numerator_date is not None
                else:
                    key = (mm.person_id, mm.measure_resolver)
                    numerator_date = numerator_dates_by_key.get(key)
                    numerator_value = key in numerator_dates_by_key

                rows.append(
                    PivotIndicatorRow(
                        person_id=mm.person_id,
                        measure_resolver=mm.measure_resolver,
                        numerator_date=numerator_date,
                        denominator_date=_coerce_date(mm.measure_date),
                        numerator_measure_id=ind.numerator_measure.measure_id,
                        denominator_measure_id=ind.denominator_measure.measure_id,
                        indicator=ind.indicator_id,
                        numerator_value=numerator_value,
                        denominator_value=True,
                    )
                )
        except Exception as e:
            if strict:
                raise
            else:
                print(f"Error processing indicator {ind.indicator_id}: {e}")

    return rows

def build_pivot_cohort(report: Report, executor: MeasureExecutor, strict: bool = True) -> list[PivotCohortRow]:
    rows: list[PivotCohortRow] = []

    for rc in report.cohorts:
        cohort_label = rc.cohort.dash_cohort_name

        for d in rc.cohort.definitions:
            m = d.dash_cohort_measure
            if not m:
                continue

            try:
                for mm in m.members(executor):
                    rows.append(
                        PivotCohortRow(
                            episode_id=mm.episode_id,
                            measure_date=mm.measure_date,
                            measure_resolver=mm.measure_resolver,
                            person_id=mm.person_id,
                            cohort_label=cohort_label,
                            subcohort_label=d.dash_cohort_def_name,
                            measure_id=m.measure_id,
                        )
                    )
            except Exception as e:
                if strict:
                    raise
                else:
                    print(f"Error processing cohort {cohort_label} definition {d.dash_cohort_def_name}: {e}")

    return rows

def build_cohort_payloads(report: Report, executor: MeasureExecutor) -> list[DashCohortPayload]:
    out = []

    for rc in report.cohorts:
        cohort = rc.cohort
        defs = []

        for d in cohort.definitions:
            m = d.dash_cohort_measure
            member_ids = [mm.person_id for mm in m.members(executor)] if m else []

            defs.append(
                DashCohortDefinitionPayload(
                    dash_cohort_def_id=d.dash_cohort_def_id,
                    dash_cohort_def_name=d.dash_cohort_def_name,
                    measure_id=d.measure_id,
                    measure_count=d.measure_count,
                    members=member_ids,
                )
            )

        out.append(
            DashCohortPayload(
                dash_cohort_id=cohort.dash_cohort_id,
                dash_cohort_name=cohort.dash_cohort_name,
                definitions=defs,
            )
        )

    return out


def build_report_payload(report: Report, executor: MeasureExecutor) -> ReportPayload:
    return ReportPayload(
        report_name=report.report_name,
        report_short_name=report.report_short_name,
        report_description=report.report_description,
        indicators=[
            IndicatorPayload(
                indicator_id=ind.indicator_id,
                indicator_description=ind.indicator_description,
                indicator_reference=ind.indicator_reference,
                numerator_label=ind.numerator_label,
                denominator_label=ind.denominator_label,
                numerator_measure=MeasureSummary(
                    id=ind.numerator_measure.measure_id,
                    measure_name=ind.numerator_measure.name,
                    measure_combination=ind.numerator_measure.combination.value,
                ),
                denominator_measure=MeasureSummary(
                    id=ind.denominator_measure.measure_id,
                    measure_name=ind.denominator_measure.name,
                    measure_combination=ind.denominator_measure.combination.value,
                ),
            )
            for ind in report.indicators
        ],
        report_cohorts=build_cohort_payloads(report, executor=executor),
        report_measures=[
            ReportMeasurePayload(
                measure_id=m.measure_id,
                materialised_measure_id=None,
                refresh_date=None,
            )
            for m in report.indicator_measures + report.cohort_measures
        ],
    )
