"""Member identity and dates shared by indicator membership and report output."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from ..core import IndicatorGrain
from .measure import MeasureMember

type MemberKey = tuple[int, int, int]


def member_key(member: MeasureMember) -> MemberKey | None:
    """Missing episode linkage never matches another cancer or a patient."""
    if member.episode_id is None:
        return None
    return member.person_id, member.episode_id, member.measure_resolver


def members_by_key(members: Sequence[MeasureMember]) -> dict[MemberKey, list[MeasureMember]]:
    """Index cancer-linked rows, retaining every evidence date and cohort anchor."""
    grouped: dict[MemberKey, list[MeasureMember]] = defaultdict(list)
    for member in members:
        key = member_key(member)
        if key is not None:
            grouped[key].append(member)
    return dict(grouped)


def member_date(value: date | datetime | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value


def member_keys(members: Iterable[MeasureMember]) -> frozenset[MemberKey]:
    return frozenset(key for member in members if (key := member_key(member)) is not None)


def earliest_date(values: Sequence[date | datetime | None]) -> date | None:
    return min((day for value in values if (day := member_date(value)) is not None), default=None)


def cohort_anchor_dates(cohort_members: Sequence[MeasureMember]) -> dict[MemberKey, date | None]:
    """Explicit denominators use the earliest cohort date per complete key."""
    dates: dict[MemberKey, list[date | None]] = defaultdict(list)
    for member in cohort_members:
        key = member_key(member)
        if key is None:
            raise ValueError("Report cohort members require episode_id for same-cancer matching")
        dates[key].append(member.measure_date)
    return {key: earliest_date(values) for key, values in dates.items()}


def indicator_grain(indicator) -> IndicatorGrain:
    """Unflushed ORM objects and old callers have the same episode default."""
    value = getattr(indicator, "grain", None)
    return IndicatorGrain.episode if value is None else IndicatorGrain(value)


@dataclass(frozen=True)
class IndicatorMemberResult:
    denominator: MeasureMember
    numerator_value: bool
    numerator_date: date | None
    eligible_keys: frozenset[MemberKey] = frozenset()
    passing_keys: frozenset[MemberKey] = frozenset()
    denominator_value: bool = True
    eligibility_status: str = "eligible"
    numerator: MeasureMember | None = None
    passing_denominator: MeasureMember | None = None
    period_date: date | None = None
    completion_date: date | None = None
    unresolved_keys: frozenset[MemberKey] = frozenset()


def reduce_patient_results(results: Sequence[IndicatorMemberResult], *, period_source="denominator") -> list[IndicatorMemberResult]:
    """Reduce already cancer-aligned eligible results; retain their audit keys."""
    by_person: dict[int, list[IndicatorMemberResult]] = defaultdict(list)
    for result in results:
        by_person[result.denominator.person_id].append(result)
    return [_patient_result(person, by_person[person], period_source=period_source) for person in sorted(by_person)]


def _patient_result(person: int, results: Sequence[IndicatorMemberResult], *, period_source) -> IndicatorMemberResult:
    eligible = [result for result in results if result.denominator_value]
    passing = [result for result in eligible if result.numerator_value]
    if any(member_key(result.denominator) is None for result in eligible):
        raise ValueError("Patient reduction requires episode-aligned input results")
    explanation = min(passing, key=lambda result: (result.numerator_date or date.max,
                                                  member_key(result.denominator))) if passing else None
    return IndicatorMemberResult(
        denominator=MeasureMember(person, person, measure_date=earliest_date(
            [result.denominator.measure_date for result in eligible])),
        numerator_value=bool(passing),
        numerator_date=earliest_date([result.numerator_date for result in passing]),
        eligible_keys=member_keys(result.denominator for result in eligible),
        passing_keys=member_keys(result.denominator for result in passing),
        denominator_value=bool(eligible),
        eligibility_status="eligible" if eligible else "unresolved",
        numerator=explanation.numerator if explanation else None,
        passing_denominator=(explanation.passing_denominator or explanation.denominator) if explanation else None,
        period_date=(explanation.period_date if explanation and period_source in ("numerator", "completion")
                     else earliest_date([result.period_date for result in eligible])),
        completion_date=explanation.completion_date if explanation else None,
        unresolved_keys=member_keys(result.denominator for result in results if not result.denominator_value),
    )
