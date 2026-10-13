import importlib

import pytest
import sqlalchemy as sa

from oa_cohorts.schema import (
    check_schema,
    create_owned_tables,
    downgrade,
    upgrade,
    upgrade_sql,
)
from tests.schema.test_schema import _seed_one_link

MIGRATION = importlib.import_module("oa_cohorts.schema.migrations.versions.0006_resolution_policy")


@pytest.mark.parametrize("initialize", [upgrade, create_owned_tables])
def test_resolution_constraints_match_models_and_reject_unknown_values(sqlite_engine, initialize):
    initialize(sqlite_engine)
    assert check_schema(sqlite_engine).is_clean
    _seed_one_link(sqlite_engine)
    for field, _, _, _ in MIGRATION.POLICIES["indicator"]:
        with pytest.raises(sa.exc.IntegrityError), sqlite_engine.begin() as db:
            db.execute(sa.text(f"UPDATE indicator SET {field}='unsupported'"))


def test_migration_preserves_predecessor_configuration_and_round_trips(sqlite_engine):
    upgrade(sqlite_engine, "0005_indicator_grain")
    _seed_one_link(sqlite_engine)
    with sqlite_engine.connect() as db:
        before = db.execute(sa.text("SELECT * FROM indicator")).mappings().all()
    upgrade(sqlite_engine)
    assert check_schema(sqlite_engine).is_clean
    with sqlite_engine.connect() as db:
        after = db.execute(sa.text("SELECT * FROM indicator")).mappings().all()
    assert [{key: row[key] for key in before[0]} for row in after] == before
    with sqlite_engine.begin() as db:
        db.execute(sa.text("UPDATE indicator SET numerator_reference='denominator'"))
    with pytest.raises(ValueError, match="nondefault resolution"):
        downgrade(sqlite_engine, "0005_indicator_grain")
    with sqlite_engine.begin() as db:
        db.execute(sa.text("UPDATE indicator SET numerator_reference='cohort'"))
    downgrade(sqlite_engine, "0005_indicator_grain")
    upgrade(sqlite_engine)
    assert check_schema(sqlite_engine).is_clean


def test_migration_is_renderable_offline(sqlite_engine):
    sql = upgrade_sql(sqlite_engine, from_revision="0005_indicator_grain")
    for policies in MIGRATION.POLICIES.values():
        assert all(field in sql for field, *_ in policies)
    assert "CREATE MATERIALIZED VIEW" not in sql
