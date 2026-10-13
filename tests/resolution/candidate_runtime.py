"""Candidate acceptance adapter: synthetic source rows, real SQL and evaluator.

Expected results are read only by the runner, never by this adapter. Pipeline
and attachment checks still use the released component implementation.
"""
from datetime import timedelta
from types import SimpleNamespace

import sqlalchemy as sa

from oa_cohorts.core import ResultDateSource, RuleCombination, WindowPickStrategy
from oa_cohorts.output.pivot_queries import build_pivot_indicators
from oa_cohorts.query.indicator import Indicator
from oa_cohorts.query.indicator_evaluation import evaluate_indicator
from oa_cohorts.query.measure import (
    Measure,
    MeasureExecutor,
    MeasureMember,
    MeasureSQLCompiler,
)
from tests.resolution.current_runtime import EPOCH, SyntheticLeaf, compile_graph
from tests.resolution.current_runtime import evaluate as evaluate_components


class EvidenceLeaf(SyntheticLeaf):
    def __init__(self, events):
        columns = {"person_id": sa.Integer, "episode_id": sa.Integer, "measure_resolver": sa.Integer,
                   "measure_date": sa.Date, "source_table": sa.String, "source_event_id": sa.BigInteger,
                   "opportunity_id": sa.BigInteger, "start_date": sa.Date, "end_date": sa.Date,
                   "date_required": sa.Boolean}
        selects = []
        for event in events:
            values = {"person_id": 1, "episode_id": 10, "measure_resolver": 10,
                      "source_table": "measurement", "date_required": True, **event}
            for name in ("measure_date", "start_date", "end_date"):
                if values.get(name) is not None:
                    values[name] = EPOCH + timedelta(days=values[name])
            selects.append(sa.select(*(sa.literal(values.get(name), type_=kind).label(name)
                                       for name, kind in columns.items())))
        if not selects:
            selects = [sa.select(*(sa.cast(sa.null(), kind).label(name) for name, kind in columns.items())).where(sa.false())]
        self.query = sa.union_all(*selects)


def leaf(events, identifier=1):
    return SimpleNamespace(measure_id=identifier, name="invented evidence", subquery=EvidenceLeaf(events),
                           children=[], combination=RuleCombination.rule_or,
                           window_config=None, is_temporal_window=False, person_ep_override=False)


def members(node, connection):
    return [MeasureMember.from_row(row) for row in connection.execute(MeasureSQLCompiler(node).sql_evidence())]


def indicator_results(denominator, numerator, cohort, **policy):
    den = Measure(measure_id=900002, name="denominator")
    num = Measure(measure_id=900001, name="numerator")
    indicator = Indicator(indicator_id=900001, indicator_description="Invented question",
                          denominator_measure=den, denominator_measure_id=den.measure_id,
                          numerator_measure=num, numerator_measure_id=num.measure_id, **policy)
    executor = MeasureExecutor(None)
    executor._cache = {den.measure_id: denominator, num.measure_id: numerator}
    results = evaluate_indicator(indicator, executor, cohort)
    pivot = build_pivot_indicators(SimpleNamespace(indicators=[indicator]), executor, cohort_members=cohort)
    assert [r.numerator_value for r in results] == [r.numerator_value for r in pivot]
    assert len(indicator.denominator_members(executor, cohort_members=cohort)) == sum(r.denominator_value for r in pivot)
    assert len(indicator.numerator_members(executor, cohort_members=cohort)) == sum(r.numerator_value for r in pivot)
    return results


def treatment_fixture(definition, connection):
    treatments = members(leaf([{"source_event_id": identifier, "opportunity_id": identifier,
                                "measure_date": day, "start_date": day}
                               for identifier, day in definition["treatments"]]), connection)
    assessments = members(leaf([{"source_event_id": identifier, "measure_date": day}
                                for identifier, day in definition["assessments"]]), connection)
    return treatments, assessments


def window_node(anchor, candidate, lower, upper, selection):
    cfg = SimpleNamespace(anchor_measure=anchor, candidate_measure=candidate,
        window_min_days=lower, window_max_days=upper, require_same_resolver=True,
        anchor_selection=selection, anchor_date_role="start", candidate_date_role="measure",
        result_date_source=ResultDateSource.candidate, window_pick_strategy=WindowPickStrategy.earliest)
    return SimpleNamespace(measure_id=999, name="invented relation", subquery=None, children=[],
                           window_config=cfg, is_temporal_window=True, person_ep_override=False)


def proposed_observations(case, connection):
    definition = case["proposed"]
    family = case["id"].split("-")[0]
    if family == "F01":
        events = [{"person_id": p, "episode_id": e, "measure_resolver": r, "measure_date": d,
                   "source_table": source.split(":")[0], "source_event_id": int(source.split(":")[1])}
                  for p, e, r, d, source in definition["events"]]
        return {"evidence_ids": [[m.source_table, m.source_event_id] for m in members(leaf(events), connection)]}
    if family == "F02":
        a = leaf([{"opportunity_id": identifier, "measure_date": 0} for identifier in definition["rt_cycles"]])
        b = leaf([{"opportunity_id": identifier, "measure_date": 0} for identifier in definition["curative_intent_cycles"]], 2)
        root = leaf([])
        root.subquery, root.children = None, [a, b]
        root.combination, root.matching_scope = RuleCombination.rule_and, "treatment"
        return {"eligible_opportunities": [[m.opportunity_id] for m in members(root, connection)]}
    if family == "F03":
        den, num = treatment_fixture(definition, connection)
        lower, upper = definition["window"]
        results = indicator_results(den, num, [MeasureMember(1, 10, 10, EPOCH)],
            opportunity_selection=definition["selection"], numerator_reference="denominator",
            numerator_max_days_prior=-lower, numerator_max_days_post=upper)
        return {"outcomes": [[r.denominator.person_id, r.denominator.episode_id, r.numerator_value,
                               r.denominator.opportunity_id, r.numerator.source_event_id if r.numerator else None]
                              for r in results]}
    if family in ("F04", "F05"):
        if family == "F04":
            treatments, assessments = definition["treatments"], definition["assessments"]
        else:
            treatments, assessments = [[201, definition["anchor_day"]]], [[301, definition["candidate_day"]]]
        anchor = leaf([{"opportunity_id": i, "source_event_id": i, "measure_date": d, "start_date": d} for i, d in treatments])
        candidate = leaf([{"source_event_id": i, "measure_date": d} for i, d in assessments], 2)
        rows = members(window_node(anchor, candidate, *definition["window"], definition.get("selection", "first")), connection)
        if family == "F04":
            return {"pairs": [[r.opportunity_id, r.candidate_event_id] for r in rows]}
        return {"date_roles": [[(r.candidate_date - EPOCH).days, (r.anchor_date - EPOCH).days,
                                 (r.completion_date - EPOCH).days] for r in rows]}
    if family == "F06":
        den = [MeasureMember(p, e, e, EPOCH + timedelta(days=d)) for p, e, d in definition["eligible"]]
        num = [MeasureMember(p, e, e, EPOCH + timedelta(days=d)) for p, e, d in definition["passing"]]
        rows = indicator_results(den, num, den, grain="patient")
        return {"passing_pairs": [[r.denominator.person_id, r.passing_denominator.episode_id,
                                    (r.passing_denominator.measure_date - EPOCH).days,
                                    (r.numerator.measure_date - EPOCH).days] for r in rows if r.numerator_value]}
    if family == "F07":
        den = [MeasureMember(p, e, e, None if d is None else EPOCH + timedelta(days=d))
               for p, e, d in definition["denominators"]]
        rows = indicator_results(den, [], [MeasureMember(1, 10, 10, EPOCH)])
        return {"eligibility_counts": [[status, sum(r.eligibility_status == status for r in rows)]
                                       for status in ("eligible", "unresolved")]}
    if family == "F08":
        nodes = compile_graph({"a": {"kind": "leaf", "events": [[1, 10, 10, definition["a_day"]]]},
                               "b": {"kind": "leaf", "events": [[1, 10, 10, definition["b_day"]]]},
                               "root": {"kind": "and", "children": ["a", "b"]}})
        den = members(nodes["root"], connection)
        rows = indicator_results(den, den, [MeasureMember(1, 10, 10, EPOCH)], period_date_source="completion")
        return {"period_outcomes": [[r.period_date.strftime("%Y-%m"), int(r.denominator_value), int(r.numerator_value)] for r in rows]}
    if family == "F09":
        a, b = (leaf([{"person_id": p, "episode_id": e, "measure_resolver": r, "measure_date": 0}
                       for p, e, r in definition[name]], i) for i, name in enumerate(("a", "b"), 1))
        root = leaf([])
        root.subquery, root.children, root.combination = None, [a, b], RuleCombination.rule_except
        return {"members": [[r.person_id, r.episode_id, r.measure_resolver] for r in members(root, connection)]}
    raise ValueError(f"Unhandled proposed fixture {family}")


def evaluate(case, connection):
    return proposed_observations(case, connection) if "proposed" in case else evaluate_components(case, connection)
