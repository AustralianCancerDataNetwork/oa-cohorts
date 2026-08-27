from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
import sqlalchemy.orm as so

from oa_cohorts.query.dash_cohort import DashCohortDef
from oa_cohorts.query.indicator import Indicator
from oa_cohorts.query.measure import Measure, MeasureRelationship
from oa_cohorts.query.report import Report, ReportIndicatorMap


@dataclass(frozen=True)
class MeasureDetailSummary:
    measure_id: int
    name: str
    combination: str
    person_ep_override: bool
    summary_kind: str
    subquery_name: str | None
    subquery_short_name: str | None
    subquery_target: str | None
    subquery_temporality: str | None
    parent_measure_names: tuple[str, ...]
    child_measure_names: tuple[str, ...]
    numerator_indicator_usages: tuple[str, ...]
    denominator_indicator_usages: tuple[str, ...]
    cohort_definition_usages: tuple[str, ...]


def has_measure_summary_tables(session: so.Session) -> bool:
    bind = session.get_bind()
    inspector = sa.inspect(bind)
    return inspector.has_table(Measure.__tablename__)


def load_measure_detail_summary(
    session: so.Session,
    *,
    measure_id: int,
) -> MeasureDetailSummary | None:
    if not has_measure_summary_tables(session):
        return None

    stmt = (
        sa.select(Measure)
        .where(Measure.measure_id == measure_id)
        .options(
            so.joinedload(Measure.subquery),
            so.selectinload(Measure.child_links).joinedload(MeasureRelationship.child),
            so.selectinload(Measure.parent_links).joinedload(MeasureRelationship.parent),
        )
    )
    measure = session.execute(stmt).scalars().unique().one_or_none()
    if measure is None:
        return None

    numerator_indicator_usages, denominator_indicator_usages = _load_indicator_usages(
        session,
        measure_id=measure_id,
    )
    cohort_definition_usages = _load_cohort_definition_usages(session, measure_id=measure_id)

    subquery = measure.subquery
    return MeasureDetailSummary(
        measure_id=measure.measure_id,
        name=measure.name,
        combination=measure.combination.value,
        person_ep_override=measure.person_ep_override,
        summary_kind=_measure_kind(measure),
        subquery_name=subquery.name if subquery is not None else None,
        subquery_short_name=subquery.short_name if subquery is not None else None,
        subquery_target=subquery.target.value if subquery is not None else None,
        subquery_temporality=subquery.temporality.value if subquery is not None else None,
        parent_measure_names=tuple(
            sorted(link.parent.name for link in measure.parent_links if link.parent is not None)
        ),
        child_measure_names=tuple(
            sorted(link.child.name for link in measure.child_links if link.child is not None)
        ),
        numerator_indicator_usages=numerator_indicator_usages,
        denominator_indicator_usages=denominator_indicator_usages,
        cohort_definition_usages=cohort_definition_usages,
    )


def _load_indicator_usages(
    session: so.Session,
    *,
    measure_id: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    bind = session.get_bind()
    inspector = sa.inspect(bind)
    if not all(inspector.has_table(table_name) for table_name in (Indicator.__tablename__, Report.__tablename__)):
        return (), ()

    stmt = (
        sa.select(Indicator)
        .where(
            sa.or_(
                Indicator.numerator_measure_id == measure_id,
                Indicator.denominator_measure_id == measure_id,
            )
        )
        .options(so.selectinload(Indicator.report_links).selectinload(ReportIndicatorMap.report))
        .order_by(Indicator.indicator_id)
    )
    indicators = session.execute(stmt).scalars().unique().all()

    numerator: list[str] = []
    denominator: list[str] = []
    for indicator in indicators:
        label = f"{indicator.indicator_id}: {indicator.indicator_description}{_report_suffix(indicator)}"
        if indicator.numerator_measure_id == measure_id:
            numerator.append(label)
        if indicator.denominator_measure_id == measure_id:
            denominator.append(label)

    return tuple(numerator), tuple(denominator)


def _report_suffix(indicator: Indicator) -> str:
    entries = []
    for link in indicator.report_links:
        if link.report is None:
            continue
        entry = f"{link.report.report_name} ({link.report.report_short_name})"
        if link.indicator_label_override is not None:
            entry += f' as "{link.indicator_label_override}"'
        entries.append(entry)

    if not entries:
        return ""
    return f" [{', '.join(sorted(entries))}]"


def _load_cohort_definition_usages(
    session: so.Session,
    *,
    measure_id: int,
) -> tuple[str, ...]:
    bind = session.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(DashCohortDef.__tablename__):
        return ()

    stmt = (
        sa.select(DashCohortDef)
        .where(DashCohortDef.measure_id == measure_id)
        .options(so.selectinload(DashCohortDef.dash_cohort_objects))
        .order_by(DashCohortDef.dash_cohort_def_id)
    )
    cohort_defs = session.execute(stmt).scalars().unique().all()

    usages: list[str] = []
    for cohort_def in cohort_defs:
        cohorts = ", ".join(sorted(cohort.dash_cohort_name for cohort in cohort_def.dash_cohort_objects))
        cohort_suffix = f" [{cohorts}]" if cohorts else ""
        usages.append(
            f"{cohort_def.dash_cohort_def_name} ({cohort_def.dash_cohort_def_short_name}){cohort_suffix}"
        )
    return tuple(usages)


def _measure_kind(measure: Measure) -> str:
    if measure.measure_id == 0:
        return "full cohort"
    if measure.subquery is not None and not measure.child_links:
        return "leaf"
    if measure.child_links:
        return "composite"
    return "standalone"
