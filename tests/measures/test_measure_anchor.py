"""Selection must occur after diagnosis filtering, before endpoint matching."""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
import sqlalchemy as sa

from oa_cohorts.core import ResultDateSource, WindowPickStrategy
from oa_cohorts.query.measure import MeasureSQLCompiler
from tests.conftest import FakeMeasure
from tests.measures.test_temporal_window import (
    FakeWindowConfig,
    _make_measure,
    _row,
    _union,
)

ORIGIN = date(2026, 1, 1)


@pytest.fixture
def pg_engine():
    url = os.environ.get("ENGINE_CDM", "")
    if not url.startswith("postgresql"):
        pytest.skip("ENGINE_CDM must point to synthetic PostgreSQL for date arithmetic")
    engine = sa.create_engine(url)
    yield engine
    engine.dispose()


def _chain(referrals, contacts, *, maximum=14):
    bounded = _make_measure(
        [_row(1, 10, 10, ORIGIN)],
        [_row(1, 10, 10, ORIGIN + timedelta(days=d)) for d in referrals],
        window_min_days=-90, window_max_days=30,
        window_pick_strategy=WindowPickStrategy.earliest,
        result_date_source=ResultDateSource.candidate,
    )
    candidate = FakeMeasure(52, "contact", subquery=type(bounded.subquery)(
        _union(*[_row(1, 10, 10, ORIGIN + timedelta(days=d)) for d in contacts])
    ))
    pathway = FakeMeasure(20020, "pathway", window_config=FakeWindowConfig(
        anchor_measure=bounded, anchor_measure_id=bounded.measure_id,
        candidate_measure=candidate, window_min_days=0, window_max_days=maximum,
        window_pick_strategy=WindowPickStrategy.earliest,
        result_date_source=ResultDateSource.candidate,
    ))
    return bounded, pathway


@pytest.mark.parametrize("referrals,contacts,maximum,expected", [
    ([-120, -10], [-110, 1], 14, 1),
    ([-120], [-110], 14, 0),
    ([-10, 0], [10], 14, 0),  # Do not choose a later referral just because it succeeds.
    ([-90], [-76], 14, 1),
    ([-91], [-77], 14, 0),
    ([30], [44], 14, 1),
    ([31], [45], 14, 0),
    ([-10], [-10], 14, 1),
    ([-10], [-11], 14, 0),
    ([-10], [5], 14, 0),
    ([-10], [32], 42, 1),
    ([-10], [33], 42, 0),
    ([405], [419], 14, 0),
    ([-59], [-49], 14, 1),
])
def test_filtered_anchor_pathway(pg_engine, referrals, contacts, maximum, expected):
    bounded, pathway = _chain(referrals, contacts, maximum=maximum)
    with pg_engine.connect() as connection:
        rows = connection.execute(MeasureSQLCompiler(pathway).sql_any()).all()
        denominator = connection.execute(MeasureSQLCompiler(bounded).sql_any()).all()
    assert len(rows) == expected
    assert len(denominator) == int(any(-90 <= d <= 30 for d in referrals))
    if rows:
        assert rows[0].measure_date == ORIGIN + timedelta(days=min(
            d for d in contacts if 0 <= d - min(r for r in referrals if -90 <= r <= 30) <= maximum
        ))


def test_measure_anchor_preserves_each_episode(pg_engine):
    bounded, pathway = _chain([-10], [1])
    second = _make_measure(
        [_row(1, 20, 20, ORIGIN)], [_row(1, 20, 20, ORIGIN + timedelta(days=5))]
    )
    bounded.window_config.window_pick_strategy = WindowPickStrategy.any
    anchor = FakeMeasure(101, "two episodes", _children=[bounded, second])
    pathway.window_config.anchor_measure = anchor
    with pg_engine.connect() as connection:
        rows = connection.execute(MeasureSQLCompiler(pathway).sql_any()).all()
    assert [(r.person_id, r.episode_id, r.measure_resolver) for r in rows] == [(1, 10, 10)]


@pytest.mark.parametrize("problem", ["no_anchor", "two_anchors", "missing_anchor", "missing_candidate", "reversed"])
def test_invalid_window_contract(problem):
    bounded, pathway = _chain([-10], [1])
    cfg = pathway.window_config
    if problem == "no_anchor":
        cfg.anchor_measure = None
        cfg.anchor_measure_id = None
    elif problem == "two_anchors":
        pathway.subquery = bounded.subquery
    elif problem == "missing_anchor":
        cfg.anchor_measure = None
    elif problem == "missing_candidate":
        cfg.candidate_measure = None
    else:
        cfg.window_min_days = 15
    with pytest.raises(ValueError):
        MeasureSQLCompiler(pathway).sql_any()


@pytest.mark.parametrize("edge", ["anchor", "candidate", "child"])
def test_cycles_report_measure_path(edge):
    bounded, pathway = _chain([-10], [1])
    if edge == "anchor":
        bounded.subquery = None
        bounded.window_config.anchor_measure = pathway
        bounded.window_config.anchor_measure_id = pathway.measure_id
    elif edge == "candidate":
        bounded.window_config.candidate_measure = pathway
    else:
        pathway.window_config.anchor_measure = FakeMeasure(102, "composite", _children=[pathway])
    with pytest.raises(ValueError, match="Measure dependency cycle"):
        MeasureSQLCompiler(pathway).sql_any()


@pytest.mark.parametrize("missing", ["referral", "diagnosis_date"])
def test_missing_evidence_cannot_supply_an_anchor(pg_engine, missing):
    bounded, pathway = _chain([-10], [1])
    if missing == "referral":
        candidate = bounded.window_config.candidate_measure
        candidate.subquery.query = candidate.subquery.query.where(sa.false())
    else:
        query = _row(1, 10, 10, None)
        bounded.subquery.query = sa.select(
            query.selected_columns.person_id,
            query.selected_columns.episode_id,
            query.selected_columns.measure_resolver,
            sa.cast(sa.null(), sa.Date).label("measure_date"),
        )
    with pg_engine.connect() as connection:
        assert connection.execute(MeasureSQLCompiler(bounded).sql_any()).all() == []
        assert connection.execute(MeasureSQLCompiler(pathway).sql_any()).all() == []


def test_duplicate_referrals_and_contacts_emit_one_canonical_pathway(pg_engine):
    bounded, pathway = _chain([-10, -10], [1, 1])
    with pg_engine.connect() as connection:
        assert len(connection.execute(MeasureSQLCompiler(bounded).sql_any()).all()) == 1
        assert len(connection.execute(MeasureSQLCompiler(pathway).sql_any()).all()) == 1
