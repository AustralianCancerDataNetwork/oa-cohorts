"""Independent clinical goldens executed by the actual compiler and report API."""
import json
import os
from collections import Counter
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.resolution import candidate_runtime

CASES = json.loads(Path(__file__).with_name("corpus.json").read_text())["cases"]


@pytest.fixture(scope="module")
def connection():
    url = os.environ.get("ENGINE_CDM")
    if not url:
        pytest.skip("ENGINE_CDM must name a local synthetic PostgreSQL database")
    parsed = sa.engine.make_url(url)
    if parsed.host not in ("localhost", "127.0.0.1") or parsed.port != 55433 or parsed.database != "postgres" or parsed.query:
        pytest.fail("Resolution goldens accept only the local synthetic container")
    engine = sa.create_engine(parsed, connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=30000"})
    with engine.connect() as db, db.begin():
        yield db
    engine.dispose()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_independent_golden(case, connection):
    with connection.begin_nested():
        observed = candidate_runtime.evaluate(case, connection)
    for name, expected in case["expected"].items():
        assert name in observed, f"Missing observation {name}"
        normalize = lambda rows: Counter(json.dumps(row, sort_keys=True) for row in rows)
        assert normalize(observed[name]) == normalize(expected), (name, observed[name], expected)


def test_null_candidates_do_not_establish_a_window(connection):
    anchor = candidate_runtime.leaf([{"measure_date": 20, "start_date": 20}])
    candidate = candidate_runtime.leaf([{"measure_date": None}], 2)
    node = candidate_runtime.window_node(anchor, candidate, None, None, "all")
    assert candidate_runtime.members(node, connection) == []


def test_bounded_conjunction_preserves_each_source_without_cartesian_growth(connection):
    from oa_cohorts.core import RuleCombination

    left = candidate_runtime.leaf([{"source_event_id": i, "measure_date": i} for i in range(32)])
    right = candidate_runtime.leaf([{"source_event_id": 100 + i, "measure_date": 20 + i} for i in range(32)], 2)
    root = candidate_runtime.leaf([])
    root.subquery, root.children, root.combination = None, [left, right], RuleCombination.rule_and
    rows = candidate_runtime.members(root, connection)
    assert len(rows) == 64
    assert {row.source_event_id for row in rows} == set(range(32)) | set(range(100, 132))
    assert min(row.completion_date for row in rows) == candidate_runtime.EPOCH + candidate_runtime.timedelta(days=20)


def test_treatment_intent_is_intrinsically_qualified_and_conflicts_fail_closed(connection, monkeypatch):
    from omop_alchemy.cdm.model import Concept

    from oa_cohorts.core import RuleTarget, RuleTemporality
    from oa_cohorts.measurables.measurable_base import (
        MeasurableBase,
        MeasurableDomain,
        MeasurableSpec,
    )
    from oa_cohorts.query.query_rule import ExactRule
    from oa_cohorts.query.subquery import Subquery

    fields = ("person_id", "episode_id", "treatment_episode_id", "intent_measurement_id", "intent", "rt", "day")
    rows = [(1, 10, 201, 1, 4162591, True, 10), (1, 10, 201, 2, 4162591, True, 10),
            (1, 10, 202, 3, 4162591, False, 10), (1, 10, 203, 4, 4179711, True, 10),
            (1, 10, 204, 5, 4162591, True, 10), (1, 10, 204, 6, 4179711, True, 10)]
    source = sa.union_all(*(sa.select(*(sa.literal(value).label(name) for name, value in zip(fields, row, strict=True)))
                            for row in rows)).subquery()

    class Intent(MeasurableBase):
        __table__ = source
        person_id, episode_id, treatment_episode_id, intent_measurement_id, intent, rt, day = source.c
        __measurable__ = MeasurableSpec(domain=MeasurableDomain.tx, label="synthetic RT intent",
            person_id_attr="person_id", episode_id_attr="episode_id", event_date_attr="day",
            value_concept_attr="intent", eligibility_attr="rt", event_id_attr="intent_measurement_id",
            opportunity_id_attr="treatment_episode_id", reject_conflicting_values=True)

    monkeypatch.setattr("oa_cohorts.query.subquery.get_measurable_registry", lambda: {RuleTarget.intent_rt: Intent})
    query = Subquery(subquery_id=1, target=RuleTarget.intent_rt, temporality=RuleTemporality.dt_rad,
                     rules=[ExactRule(query_rule_id=1, concept_id=4162591, concept=Concept(concept_id=4162591))])
    # Canonical query keeps day numeric here; the real intrinsic predicate is
    # tested independently of the date cast used by physical Construct views.
    statement = sa.select(Intent.treatment_episode_id, Intent.intent_measurement_id).where(query.where_clause())
    assert sorted(tuple(row) for row in connection.execute(statement)) == [(201, 1), (201, 2)]


def test_first_start_keeps_its_own_end_even_when_a_later_course_finishes_first(connection):
    anchor = candidate_runtime.leaf([
        {"opportunity_id": 201, "source_event_id": 1, "measure_date": 10, "start_date": 10, "end_date": 60},
        {"opportunity_id": 202, "source_event_id": 2, "measure_date": 20, "start_date": 20, "end_date": 40}])
    candidate = candidate_runtime.leaf([{"source_event_id": 3, "measure_date": 61}], 2)
    node = candidate_runtime.window_node(anchor, candidate, 0, 2, "first")
    node.window_config.anchor_order_date_role = "start"
    node.window_config.anchor_date_role = "end"
    rows = candidate_runtime.members(node, connection)
    assert [(row.opportunity_id, row.anchor_date, row.end_date) for row in rows] == [
        (201, candidate_runtime.EPOCH + candidate_runtime.timedelta(days=60),
         candidate_runtime.EPOCH + candidate_runtime.timedelta(days=60))]


def test_explicit_presence_policy_passes_unbounded_only():
    from tests.indicators.test_member_matching import evaluate, member

    for window, expected in (({}, True), ({"numerator_max_days_post": 10}, False)):
        indicator, executor, rows = evaluate([member()], [member(day=None)], [member()],
                                            undated_numerator_policy="presence", **window)
        assert rows[0].numerator_value == expected
        assert bool(indicator.numerator_members(executor, cohort_members=[member()])) == expected


def test_executor_cache_is_bound_to_people_and_override():
    from oa_cohorts.query.measure import MeasureExecutor

    node = candidate_runtime.leaf([{"person_id": p, "measure_date": p} for p in (1, 2)])
    with sa.create_engine("sqlite://").connect() as db:
        executor = MeasureExecutor(db)
        assert [m.person_id for m in executor.execute(node, people=[1])] == [1]
        assert [m.person_id for m in executor.execute(node, people=[2])] == [2]
        assert executor.execute(node, people=[]) == []
        assert {m.person_id for m in executor.execute(node)} == {1, 2}
        prior = executor.execute(node)
        assert executor.execute(node) is prior
        assert executor.execute(node, ep_override=True) is not prior
