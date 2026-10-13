"""Exercise current Cohorts code with synthetic leaf queries, not a reference engine."""
from datetime import date, timedelta
from types import SimpleNamespace

import sqlalchemy as sa

from oa_cohorts.core import ResultDateSource, RuleCombination, WindowPickStrategy
from oa_cohorts.output.pivot_queries import build_pivot_indicators
from oa_cohorts.query.indicator import Indicator
from oa_cohorts.query.measure import (
    Measure,
    MeasureExecutor,
    MeasureMember,
    MeasureSQLCompiler,
)
from oa_cohorts.query.typing import _first_member_query

EPOCH = date(2026, 1, 1)


def member_query(events):
    """Only the source/leaf boundary is synthetic; compiler predicates remain real."""
    columns = [sa.column(n, t) for n, t in (
        ("person_id", sa.Integer), ("episode_id", sa.Integer),
        ("measure_resolver", sa.Integer), ("measure_date", sa.Date),
    )]
    if not events:
        return sa.select(*(sa.cast(sa.null(), c.type).label(c.name) for c in columns)).where(sa.false())
    values = [(*row[:3], sa.cast(sa.null(), sa.Date) if row[3] is None else EPOCH + timedelta(days=row[3])) for row in events]
    table = sa.values(*columns).data(values).alias("synthetic_events")
    return sa.select(*table.c)


class SyntheticLeaf:
    """Canonical leaf contract with a real grouped FIRST query."""
    def __init__(self, events):
        self.query = member_query(events)

    def get_subquery_any(self, *, ep_override=False):
        return self.query

    def get_subquery_first(self, *, ep_override=False):
        return _first_member_query(self.query)


def compile_graph(plan):
    nodes = {}
    for name, definition in plan.items():
        kind = definition["kind"]
        node = SimpleNamespace(
            measure_id=len(nodes) + 1, name=name, children=[], subquery=None,
            combination=RuleCombination.rule_or, person_ep_override=False,
            window_config=None, is_temporal_window=kind == "window",
        )
        if kind == "leaf":
            node.subquery = SyntheticLeaf(definition["events"])
        elif kind in ("and", "or"):
            node.children = [nodes[child] for child in definition["children"]]
            node.combination = getattr(RuleCombination, f"rule_{kind}")
        elif kind == "window":
            node.window_config = window_config(definition, nodes)
        else:
            raise ValueError(f"Unsupported current-runtime node: {kind}")
        nodes[name] = node
    return nodes


def window_config(definition, nodes):
    anchor = nodes[definition["anchor"]]
    candidate = nodes[definition["candidate"]]
    return SimpleNamespace(
        anchor_measure=anchor, anchor_measure_id=anchor.measure_id,
        candidate_measure=candidate, candidate_measure_id=candidate.measure_id,
        window_min_days=definition.get("min"), window_max_days=definition.get("max"),
        window_pick_strategy=WindowPickStrategy(definition.get("pick", "any")),
        result_date_source=ResultDateSource(definition.get("date_source", "candidate")),
        require_same_resolver=True,
    )


def measure_rows(node, variant, connection):
    compiler = MeasureSQLCompiler(node)
    statement = compiler.sql_first() if variant == "first" else compiler.sql_any()
    return [
        [row.person_id, row.episode_id, row.measure_resolver,
         None if row.measure_date is None else (row.measure_date - EPOCH).days]
        for row in connection.execute(statement)
    ]


def members(rows):
    return [MeasureMember(r[0], r[2], r[1], None if r[3] is None else EPOCH + timedelta(days=r[3])) for r in rows]


def resolve_indicator(definition, streams):
    numerator = Measure(measure_id=900001, name="synthetic numerator")
    denominator = Measure(measure_id=0 if definition["denominator"] == "cohort" else 900002,
                          name="synthetic denominator")
    indicator = Indicator(
        indicator_id=900001, indicator_description="Synthetic resolution case",
        numerator_measure=numerator, numerator_measure_id=numerator.measure_id,
        denominator_measure=denominator, denominator_measure_id=denominator.measure_id,
        grain=definition.get("grain", "episode"), **definition.get("windows", {}),
    )
    executor = MeasureExecutor(None)
    executor._cache = {numerator.measure_id: members(streams[definition["numerator"]])}
    if denominator.measure_id:
        executor._cache[denominator.measure_id] = members(streams[definition["denominator"]])
    cohort = members(definition["cohort"])
    rows = build_pivot_indicators(SimpleNamespace(indicators=[indicator]), executor, cohort_members=cohort)
    outcomes = [
        [r.person_id, r.measure_resolver, r.numerator_value,
         None if r.denominator_date is None else (r.denominator_date - EPOCH).days,
         None if r.numerator_date is None else (r.numerator_date - EPOCH).days]
        for r in rows
    ]
    api_denominator = indicator.denominator_members(executor, cohort_members=cohort)
    api_numerator = indicator.numerator_members(executor, cohort_members=cohort)
    return {
        "indicator_rows": outcomes,
        "api_eligible_units": sorted({(m.person_id, m.episode_id, m.measure_resolver) for m in api_denominator}),
        "api_passing_units": sorted({(m.person_id, m.episode_id, m.measure_resolver) for m in api_numerator}),
    }


def evaluate(case, connection):
    """Adapter protocol: named observations; absent observations are blockers."""
    plan = case.get("current_runtime")
    if plan is None:
        return {}
    nodes = compile_graph(plan["measures"])
    observed = {}
    for checkpoint in plan.get("measure_checks", []):
        name, variant = checkpoint.split(":")
        observed[checkpoint] = measure_rows(nodes[name], variant, connection)
    if "indicator" in plan:
        definition = plan["indicator"]
        names = {definition["numerator"], definition["denominator"]} - {"cohort"}
        streams = {name: measure_rows(nodes[name], "any", connection) for name in names}
        observed.update(resolve_indicator(definition, streams))
    return observed
