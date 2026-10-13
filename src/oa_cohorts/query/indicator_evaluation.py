"""One denominator-first evaluator for membership APIs and serialized reports."""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import replace
from datetime import date, timedelta

from ..core import IndicatorGrain
from .indicator_members import (
    IndicatorMemberResult,
    cohort_anchor_dates,
    indicator_grain,
    member_date,
    member_keys,
    members_by_key,
    reduce_patient_results,
)


def evidence_order(member):
    return (member_date(member.measure_date) or date.max, member.opportunity_id or -1,
            member.source_table or "", member.source_event_id or -1)


def policy(indicator, name, default):
    return getattr(indicator, name, None) or default


def validate_resolution_policy(indicator):
    options = {"opportunity_selection": ("any", "first"),
               "numerator_reference": ("cohort", "denominator"),
               "period_date_source": ("cohort", "denominator", "numerator", "completion"),
               "undated_numerator_policy": ("exclude", "presence")}
    for name, choices in options.items():
        value = getattr(indicator, name, None)
        if value is not None and value not in choices:
            raise ValueError(f"Invalid {name}: {value!r}; choose from {choices}")


def evaluate_indicator(indicator, executor, cohort_members=None, *, grain=None):
    validate_resolution_policy(indicator)
    if cohort_members is None:
        if getattr(indicator, "denominator_measure_id", None) == 0 or any(
            getattr(indicator, f"{side}_max_days_{bound}", None) is not None
            for side in ("numerator", "denominator") for bound in ("prior", "post")
        ):
            raise ValueError("Explicit report cohort_members scope is required for whole-cohort matching or indicator windows")
        if indicator_grain(indicator) == IndicatorGrain.patient:
            raise ValueError("Patient indicators require explicit report cohort_members scope")
        cohort_members = indicator.denominator_measure.members(executor)
    anchors = cohort_anchor_dates(cohort_members)
    whole = getattr(indicator, "denominator_measure_id", getattr(indicator.denominator_measure, "measure_id", None)) == 0
    denominators = members_by_key(cohort_members if whole else indicator.denominator_measure.members(executor))
    numerators = members_by_key(indicator.numerator_measure.members(executor))
    results = []
    for key, candidates in denominators.items():
        if key not in anchors:
            continue
        result = evaluate_cancer(indicator, candidates, numerators.get(key, ()), anchors[key], whole=whole)
        if result is not None:
            results.append(result)
    resolved_grain = grain or indicator_grain(indicator)
    return reduce_patient_results(results, period_source=policy(indicator, "period_date_source", "denominator")) if resolved_grain == IndicatorGrain.patient else results


def evaluate_cancer(indicator, candidates, numerators, cohort_date, *, whole):
    dated = [member for member in candidates if member.measure_date is not None]
    eligible = [member for member in dated if within_window(indicator, 
        member.measure_date, anchor_date=cohort_date, side="denominator")]
    if not eligible:
        needs_reference = any(getattr(indicator, f"denominator_max_days_{bound}", None) is not None
                              for bound in ("prior", "post"))
        if candidates and (not dated or (needs_reference and cohort_date is None)):
            return IndicatorMemberResult(min(candidates, key=evidence_order), False, None,
                denominator_value=False, eligibility_status="unresolved",
                unresolved_keys=member_keys(candidates[:1]))
        return None
    eligible.sort(key=evidence_order)
    if policy(indicator, "opportunity_selection", "any") == "first" or whole:
        eligible = eligible[:1]
    denominator = eligible[0]
    pair = passing_pair(indicator, eligible, numerators, cohort_date)
    numerator = pair[1] if pair else None
    explanation = pair[0] if pair else denominator
    keys = member_keys([denominator])
    completion = None
    if numerator is not None:
        dates = [qualification_date(explanation),
                 qualification_date(numerator)]
        completion = max(dates) if all(value is not None for value in dates) else None
    period_dates = {"cohort": cohort_date, "denominator": member_date(denominator.measure_date),
                    "numerator": member_date(numerator.measure_date) if numerator else None,
                    "completion": completion if pair else qualification_date(denominator)}
    return IndicatorMemberResult(denominator, bool(pair), member_date(numerator.measure_date) if numerator else None,
        eligible_keys=keys, passing_keys=keys if pair else frozenset(),
        numerator=numerator, passing_denominator=explanation if pair else None,
        period_date=period_dates[policy(indicator, "period_date_source", "denominator")], completion_date=completion)


def qualification_date(member):
    if member.completion_date is not None or member.source_date is not None or member.source_table is not None:
        return member_date(member.completion_date)
    return member_date(member.measure_date)


def passing_pair(indicator, denominators, numerators, cohort_date):
    reference = policy(indicator, "numerator_reference", "cohort")
    usable = [member for member in numerators if member.measure_date is not None
              or policy(indicator, "undated_numerator_policy", "exclude") == "presence"]
    usable.sort(key=evidence_order)
    if reference == "cohort":
        numerator = next((member for member in usable if within_window(indicator, 
            member.measure_date, anchor_date=cohort_date, side="numerator")), None)
        return (denominators[0], numerator) if numerator is not None else None
    by_opportunity = {}
    for member in usable:
        by_opportunity.setdefault(member.opportunity_id, []).append(member)
    indexes = {key: ([member_date(m.measure_date) or date.max for m in values], values)
               for key, values in by_opportunity.items()}
    for denominator in denominators:
        matches = [first_in_window(indicator, indexes.get(key), denominator.measure_date)
                   for key in dict.fromkeys((None, denominator.opportunity_id))]
        passing = [member for member in matches if member is not None]
        if passing:
            return denominator, min(passing, key=evidence_order)
    return None


def first_in_window(indicator, index, anchor):
    """Find the first candidate in O(log n), without pairing every source row."""
    if index is None:
        return None
    dates, values = index
    prior = getattr(indicator, "numerator_max_days_prior", None)
    anchor_day = member_date(anchor)
    if prior is not None and anchor_day is None:
        return None
    lower = anchor_day - timedelta(days=prior) if prior is not None and anchor_day else date.min
    offset = bisect_left(dates, lower)
    if offset < len(values) and within_window(indicator, values[offset].measure_date,
                                              anchor_date=anchor, side="numerator"):
        return values[offset]
    return None


def within_window(indicator, value, *, anchor_date, side):
    prior = getattr(indicator, f"{side}_max_days_prior", None)
    post = getattr(indicator, f"{side}_max_days_post", None)
    if prior is None and post is None:
        return True
    value, anchor = member_date(value), member_date(anchor_date)
    if value is None or anchor is None:
        return False
    return ((prior is None or value >= anchor - timedelta(days=prior))
            and (post is None or value <= anchor + timedelta(days=post)))


def outcome_member(result, *, numerator=False):
    if numerator:
        return replace(result.numerator, person_id=result.denominator.person_id,
                       episode_id=result.denominator.episode_id,
                       measure_resolver=result.denominator.measure_resolver,
                       measure_date=result.numerator_date)
    return result.denominator


def outcome_members(results, *, numerator):
    return [outcome_member(result, numerator=numerator) for result in results
            if (result.numerator_value if numerator else result.denominator_value)]
