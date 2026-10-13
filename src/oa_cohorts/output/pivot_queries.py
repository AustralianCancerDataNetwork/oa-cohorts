from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import TYPE_CHECKING

from ..query.indicator_evaluation import evaluate_indicator, policy
from ..query.indicator_members import indicator_grain
from ..query.measure import MeasureExecutor, MeasureMember
from ..query.report import Report, ReportIndicatorMap
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


def _coerce_date(value: date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


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
    for ind in report.indicators:
        try:
            results = evaluate_indicator(ind, executor, resolved_cohort_members)
            rows.extend(
                PivotIndicatorRow(
                    person_id=result.denominator.person_id,
                    episode_id=result.denominator.episode_id,
                    measure_resolver=result.denominator.measure_resolver,
                    grain=indicator_grain(ind),
                    numerator_date=_coerce_date(result.numerator_date),
                    denominator_date=_coerce_date(result.denominator.measure_date),
                    period_date=_coerce_date(result.period_date),
                    completion_date=_coerce_date(result.completion_date),
                    eligibility_status=result.eligibility_status,
                    eligible_cancer_keys=sorted(result.eligible_keys),
                    passing_cancer_keys=sorted(result.passing_keys),
                    unresolved_cancer_keys=sorted(result.unresolved_keys),
                    passing_denominator_date=_coerce_date(result.passing_denominator.measure_date) if result.passing_denominator else None,
                    opportunity_id=result.passing_denominator.opportunity_id if result.passing_denominator else result.denominator.opportunity_id,
                    numerator_source_event_id=result.numerator.source_event_id if result.numerator else None,
                    anchor_date=_coerce_date(result.numerator.anchor_date) if result.numerator else None,
                    candidate_date=_coerce_date(result.numerator.candidate_date) if result.numerator else None,
                    numerator_measure_id=ind.numerator_measure.measure_id,
                    denominator_measure_id=ind.denominator_measure.measure_id,
                    indicator=ind.indicator_id,
                    numerator_value=result.numerator_value,
                    denominator_value=result.denominator_value,
                )
                for result in results
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


def _build_indicator_payload(link: ReportIndicatorMap) -> IndicatorPayload:
    ind = link.indicator

    return IndicatorPayload(
        indicator_id=ind.indicator_id,
        indicator_description=ind.indicator_description,
        indicator_reference=ind.indicator_reference,
        grain=indicator_grain(ind),
        opportunity_selection=policy(ind, "opportunity_selection", "any"),
        numerator_reference=policy(ind, "numerator_reference", "cohort"),
        period_date_source=policy(ind, "period_date_source", "denominator"),
        undated_numerator_policy=policy(ind, "undated_numerator_policy", "exclude"),
        display_label=link.label,
        display_reference=link.reference,
        display_benchmark=link.benchmark,
        display_benchmark_unit=link.benchmark_unit,
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


def build_report_payload(report: Report, executor: MeasureExecutor) -> ReportPayload:
    return ReportPayload(
        report_name=report.report_name,
        report_short_name=report.report_short_name,
        report_description=report.report_description,
        indicators=[
            _build_indicator_payload(link)
            for link in report.indicator_links
            if link.indicator is not None
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
