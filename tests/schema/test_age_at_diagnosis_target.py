"""Age-at-diagnosis target: revision 0008 upgrade/downgrade guard."""

import pytest

from oa_cohorts.schema import current_revision, downgrade, upgrade

PREVIOUS = "0007_observation_value_target"
REVISION = "0008_age_at_diagnosis_target"


def _exercise(engine):
    upgrade(engine, PREVIOUS)
    upgrade(engine, REVISION)
    assert current_revision(engine) == REVISION

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO subquery (subquery_id, target, temporality, name) "
            "VALUES (1, 'demog_age_at_dx', 'dt_any', 'Age over 75')"
        )
        connection.exec_driver_sql(
            "INSERT INTO query_rule (query_rule_id, matcher, concept_id, scalar_threshold, "
            "threshold_direction, threshold_comparator) VALUES (1, 'scalar', 0, 75, 'gt', 'demog_age_at_dx')"
        )

    # Either reference blocks the downgrade on its own.
    with pytest.raises(ValueError, match="Remove age-at-diagnosis rules"):
        downgrade(engine, PREVIOUS)
    with engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM subquery WHERE subquery_id = 1")
    with pytest.raises(ValueError, match="Remove age-at-diagnosis rules"):
        downgrade(engine, PREVIOUS)
    assert current_revision(engine) == REVISION

    with engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM query_rule WHERE query_rule_id = 1")
    downgrade(engine, PREVIOUS)
    assert current_revision(engine) == PREVIOUS


def test_age_target_upgrade_and_guarded_downgrade_sqlite(sqlite_engine):
    _exercise(sqlite_engine)


def test_age_target_upgrade_and_guarded_downgrade_postgres(pg_engine):
    _exercise(pg_engine)
