import pytest
import sqlalchemy as sa

from oa_cohorts.query.indicator import Indicator
from oa_cohorts.schema import (
    check_schema,
    create_owned_tables,
    current_revision,
    downgrade,
    upgrade,
    upgrade_sql,
)
from tests.schema.test_schema import _seed_one_link


def test_grain_migration_backfills_and_keeps_old_configuration(sqlite_engine):
    upgrade(sqlite_engine, "0004_window_measure_anchor")
    _seed_one_link(sqlite_engine)
    upgrade(sqlite_engine)
    with sqlite_engine.connect() as connection:
        assert connection.execute(sa.text("SELECT grain FROM indicator")).scalars().all() == ["episode"]
    assert check_schema(sqlite_engine).is_clean


@pytest.mark.parametrize("initialise", [upgrade, create_owned_tables])
def test_grain_check_is_reconciled_and_enforced_for_both_creation_paths(sqlite_engine, initialise):
    initialise(sqlite_engine)
    assert check_schema(sqlite_engine).is_clean
    _seed_one_link(sqlite_engine)
    with pytest.raises(sa.exc.IntegrityError), sqlite_engine.begin() as connection:
        connection.execute(sa.text("UPDATE indicator SET grain='person'"))


def test_patient_configuration_blocks_downgrade(sqlite_engine):
    upgrade(sqlite_engine)
    _seed_one_link(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("UPDATE indicator SET grain='patient'"))
    with pytest.raises(ValueError, match="Remove patient-grain"):
        downgrade(sqlite_engine, "0004_window_measure_anchor")
    assert current_revision(sqlite_engine) == "0005_indicator_grain"
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("UPDATE indicator SET grain='episode'"))
    downgrade(sqlite_engine, "0004_window_measure_anchor")
    assert "grain" not in {c["name"] for c in sa.inspect(sqlite_engine).get_columns("indicator")}


@pytest.mark.parametrize("value", ["person", "", None])
def test_invalid_grain_rejected_in_model_and_database(sqlite_engine, value):
    with pytest.raises((ValueError, TypeError)):
        Indicator(grain=value)
    upgrade(sqlite_engine)
    _seed_one_link(sqlite_engine)
    with pytest.raises(sa.exc.IntegrityError), sqlite_engine.begin() as connection:
        connection.execute(sa.text("UPDATE indicator SET grain=:value"), {"value": value})


def test_grain_migration_is_renderable_offline(sqlite_engine):
    sql = upgrade_sql(sqlite_engine, from_revision="0004_window_measure_anchor")
    assert "grain" in sql and "episode" in sql and "patient" in sql
    assert "CREATE MATERIALIZED VIEW" not in sql
