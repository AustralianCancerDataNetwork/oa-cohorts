from __future__ import annotations

from dataclasses import dataclass

import pytest
import sqlalchemy as sa

from oa_cohorts.core import ResultDateSource, WindowPickStrategy
from oa_cohorts.query.measure import MeasureSQLCompiler
from tests.conftest import FakeMeasure, FakeSubquery


@dataclass
class FakeWindowConfig:
    candidate_measure: object
    candidate_measure_id: int = 99
    window_min_days: int | None = None
    window_max_days: int | None = None
    window_pick_strategy: WindowPickStrategy | None = None
    result_date_source: ResultDateSource | None = None
    require_same_resolver: bool = True


def _row(person_id, episode_id, resolver, measure_date: str):
    return sa.select(
        sa.literal(person_id).label("person_id"),
        sa.literal(episode_id).label("episode_id"),
        sa.literal(resolver).label("measure_resolver"),
        sa.literal(measure_date).label("measure_date"),
    )


def _union(*rows):
    return rows[0] if len(rows) == 1 else sa.union_all(*rows)


def _make_measure(anchor_rows, candidate_rows, **window_kwargs):
    anchor_q = _union(*anchor_rows) if len(anchor_rows) > 1 else anchor_rows[0]
    candidate_q = _union(*candidate_rows) if len(candidate_rows) > 1 else candidate_rows[0]
    candidate = FakeMeasure(
        measure_id=99, name="candidate", subquery=FakeSubquery(candidate_q)
    )
    cfg = FakeWindowConfig(candidate_measure=candidate, **window_kwargs)
    return FakeMeasure(
        measure_id=1, name="anchor", subquery=FakeSubquery(anchor_q), window_config=cfg
    )


@pytest.fixture
def engine():
    return sa.create_engine("sqlite://")


# ---------------------------------------------------------------------------
# Compilation structure tests (no DB required)
# ---------------------------------------------------------------------------

def test_null_bounds_produce_no_interval_predicate():
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01")],
        window_min_days=None,
        window_max_days=None,
    )
    sql = str(MeasureSQLCompiler(m).sql_any().compile(compile_kwargs={"literal_binds": True}))
    assert "INTERVAL" not in sql


def test_both_bounds_produce_two_interval_predicates():
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01")],
        window_min_days=-30,
        window_max_days=42,
    )
    sql = str(MeasureSQLCompiler(m).sql_any().compile(compile_kwargs={"literal_binds": True}))
    assert sql.count("INTERVAL") == 2


def test_pick_any_produces_no_group_by():
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01")],
        window_pick_strategy=WindowPickStrategy.any,
    )
    sql = str(MeasureSQLCompiler(m).sql_any().compile(compile_kwargs={"literal_binds": True}))
    assert "GROUP BY" not in sql


def test_pick_earliest_uses_row_number():
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01")],
        window_pick_strategy=WindowPickStrategy.earliest,
    )
    sql = str(MeasureSQLCompiler(m).sql_any().compile(compile_kwargs={"literal_binds": True}))
    assert "row_number" in sql.lower()
    assert "over" in sql.lower()
    assert "GROUP BY" not in sql


def test_pick_latest_uses_row_number():
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01")],
        window_pick_strategy=WindowPickStrategy.latest,
    )
    sql = str(MeasureSQLCompiler(m).sql_any().compile(compile_kwargs={"literal_binds": True}))
    assert "row_number" in sql.lower()
    assert "over" in sql.lower()
    assert "GROUP BY" not in sql


def test_pick_closest_uses_row_number():
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01")],
        window_pick_strategy=WindowPickStrategy.closest,
    )
    sql = str(MeasureSQLCompiler(m).sql_any().compile(compile_kwargs={"literal_binds": True}))
    assert "row_number" in sql.lower()
    assert "over" in sql.lower()


def test_result_source_greatest_uses_greatest_func():
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-03-01")],
        result_date_source=ResultDateSource.greatest,
    )
    sql = str(MeasureSQLCompiler(m).sql_any().compile(compile_kwargs={"literal_binds": True}))
    assert "greatest" in sql.lower()


def test_require_same_resolver_false_omits_resolver_join_condition():
    m_true = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01")],
        require_same_resolver=True,
    )
    m_false = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01")],
        require_same_resolver=False,
    )
    sql_true = str(MeasureSQLCompiler(m_true).sql_any().compile(compile_kwargs={"literal_binds": True}))
    sql_false = str(MeasureSQLCompiler(m_false).sql_any().compile(compile_kwargs={"literal_binds": True}))
    # With resolver constraint the join ON clause adds an extra measure_resolver equality;
    # without it, measure_resolver only appears in the SELECT column list.
    assert sql_true.count("measure_resolver") > sql_false.count("measure_resolver")


def test_sql_undated_raises():
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01")],
    )
    with pytest.raises(NotImplementedError):
        MeasureSQLCompiler(m).sql_undated()


# ---------------------------------------------------------------------------
# SQLite execution tests (NULL bounds — no dialect-specific date arithmetic)
# ---------------------------------------------------------------------------

def test_execution_returns_matching_row(engine):
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01")],
    )
    with engine.connect() as conn:
        rows = conn.execute(MeasureSQLCompiler(m).sql_any()).all()
    assert len(rows) == 1
    assert rows[0].person_id == 1


def test_execution_no_match_when_person_differs(engine):
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(2, 20, 20, "2024-02-01")],
    )
    with engine.connect() as conn:
        rows = conn.execute(MeasureSQLCompiler(m).sql_any()).all()
    assert len(rows) == 0


def test_require_same_resolver_excludes_mismatched_resolver(engine):
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 20, 20, "2024-02-01")],
        require_same_resolver=True,
    )
    with engine.connect() as conn:
        rows = conn.execute(MeasureSQLCompiler(m).sql_any()).all()
    assert len(rows) == 0


def test_require_same_resolver_false_matches_across_resolvers(engine):
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 20, 20, "2024-02-01")],
        require_same_resolver=False,
    )
    with engine.connect() as conn:
        rows = conn.execute(MeasureSQLCompiler(m).sql_any()).all()
    assert len(rows) == 1


def test_pick_any_returns_all_candidates(engine):
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01"), _row(1, 10, 10, "2024-03-01")],
        window_pick_strategy=WindowPickStrategy.any,
    )
    with engine.connect() as conn:
        rows = conn.execute(MeasureSQLCompiler(m).sql_any()).all()
    assert len(rows) == 2


def test_pick_earliest_returns_min_date(engine):
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01"), _row(1, 10, 10, "2024-03-01")],
        window_pick_strategy=WindowPickStrategy.earliest,
    )
    with engine.connect() as conn:
        rows = conn.execute(MeasureSQLCompiler(m).sql_any()).all()
    assert len(rows) == 1
    assert str(rows[0].measure_date) == "2024-02-01"


def test_pick_latest_returns_max_date(engine):
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01"), _row(1, 10, 10, "2024-03-01")],
        window_pick_strategy=WindowPickStrategy.latest,
    )
    with engine.connect() as conn:
        rows = conn.execute(MeasureSQLCompiler(m).sql_any()).all()
    assert len(rows) == 1
    assert str(rows[0].measure_date) == "2024-03-01"


def test_result_date_source_candidate_emits_candidate_date(engine):
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-03-01")],
        result_date_source=ResultDateSource.candidate,
        window_pick_strategy=WindowPickStrategy.any,
    )
    with engine.connect() as conn:
        rows = conn.execute(MeasureSQLCompiler(m).sql_any()).all()
    assert str(rows[0].measure_date) == "2024-03-01"


def test_result_date_source_anchor_emits_anchor_date(engine):
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-03-01")],
        result_date_source=ResultDateSource.anchor,
        window_pick_strategy=WindowPickStrategy.any,
    )
    with engine.connect() as conn:
        rows = conn.execute(MeasureSQLCompiler(m).sql_any()).all()
    assert str(rows[0].measure_date) == "2024-01-01"


def test_sql_first_deduplicates_to_one_row_per_resolver(engine):
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01")],
        [_row(1, 10, 10, "2024-02-01"), _row(1, 10, 10, "2024-03-01")],
        window_pick_strategy=WindowPickStrategy.any,
    )
    with engine.connect() as conn:
        rows = conn.execute(MeasureSQLCompiler(m).sql_first()).all()
    assert len(rows) == 1
    assert str(rows[0].measure_date) == "2024-02-01"


def test_sql_first_across_multiple_resolvers(engine):
    m = _make_measure(
        [_row(1, 10, 10, "2024-01-01"), _row(1, 20, 20, "2024-01-15")],
        [
            _row(1, 10, 10, "2024-02-01"),
            _row(1, 10, 10, "2024-03-01"),
            _row(1, 20, 20, "2024-04-01"),
        ],
        window_pick_strategy=WindowPickStrategy.any,
    )
    with engine.connect() as conn:
        rows = conn.execute(MeasureSQLCompiler(m).sql_first()).all()
    resolvers = {r.measure_resolver for r in rows}
    assert resolvers == {10, 20}
    assert len(rows) == 2
