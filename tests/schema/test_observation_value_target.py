"""Observation value enum migration and read compatibility at revision 0006."""

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from oa_cohorts.core import RuleTarget
from oa_cohorts.query.measure import Measure, MeasureSQLCompiler
from oa_cohorts.query.subquery import Subquery
from oa_cohorts.schema import (
    bootstrap_query_schema,
    current_revision,
    downgrade,
    upgrade,
)

PREVIOUS = "0006_resolution_policy"
REVISION = "0007_observation_value_target"


def test_observation_value_target_upgrade_and_downgrade(pg_engine):
    upgrade(pg_engine, PREVIOUS)
    upgrade(pg_engine, REVISION)
    assert current_revision(pg_engine) == REVISION

    with pg_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO subquery (subquery_id, target, temporality, name) "
            "VALUES (1, 'obs_value_concept', 'dt_obs', 'Observation value')"
        )
        connection.exec_driver_sql(
            "INSERT INTO query_rule (query_rule_id, matcher, concept_id) VALUES (1, 'obs_context', 43054909)"
        )
        connection.exec_driver_sql(
            "INSERT INTO subquery_rule_map (subquery_id, query_rule_id) VALUES (1, 1)"
        )

    from oa_cohorts.query import ObservationContextRule

    with Session(pg_engine) as session:
        subquery = session.get(Subquery, 1)
        assert isinstance(subquery.rules[0], ObservationContextRule)
        assert subquery.rules[0].concept_id == 43054909

    with pytest.raises(ValueError, match="Remove observation value rules"):
        downgrade(pg_engine, PREVIOUS)
    assert current_revision(pg_engine) == REVISION

    with pg_engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM subquery_rule_map WHERE subquery_id = 1")
        connection.exec_driver_sql("DELETE FROM subquery WHERE subquery_id = 1")
    with pytest.raises(ValueError, match="Remove observation value rules"):
        downgrade(pg_engine, PREVIOUS)
    with pg_engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM query_rule WHERE query_rule_id = 1")
    downgrade(pg_engine, PREVIOUS)
    assert current_revision(pg_engine) == PREVIOUS


def test_revision_0006_still_reads_and_compiles_with_0007_models(pg_engine):
    upgrade(pg_engine, PREVIOUS)
    with pg_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO subquery (subquery_id, target, temporality, name) "
            "VALUES (1, 'dx_any', 'dt_any', 'Old target')"
        )
        connection.exec_driver_sql(
            "INSERT INTO query_rule (query_rule_id, matcher) VALUES (1, 'presence')"
        )
        connection.exec_driver_sql(
            "INSERT INTO subquery_rule_map (subquery_id, query_rule_id) VALUES (1, 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO measure (measure_id, name, combination, subquery_id, person_ep_override) "
            "VALUES (1, 'Old measure', 'rule_or', 1, false)"
        )

    assert bootstrap_query_schema(pg_engine).action == "unchanged"
    with pg_engine.connect() as connection, Session(connection) as session:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        measure = session.get(Measure, 1)
        assert measure.subquery.target is RuleTarget.dx_any
        assert "test_events" in str(MeasureSQLCompiler(measure).sql_evidence().compile(dialect=pg_engine.dialect))
        assert measure.is_executable().status.value == "pass"
        assert "obs_value_concept" not in connection.scalars(
            sa.text("SELECT unnest(enum_range(NULL::ruletarget))::text")
        ).all()
        assert "obs_context" not in connection.scalars(
            sa.text("SELECT unnest(enum_range(NULL::rulematcher))::text")
        ).all()
    assert current_revision(pg_engine) == PREVIOUS
