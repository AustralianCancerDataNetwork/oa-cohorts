"""Schema scoping, the Alembic baseline, and the workflow guard."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
import typer
from orm_loader.helpers import Base
from rich.console import Console

from oa_cohorts.cli.schema import (
    GuardMode,
    bootstrap_query_schema,
    explain_schema_failure,
    guard_schema,
)
from oa_cohorts.schema import (
    check_schema,
    create_owned_tables,
    current_revision,
    downgrade,
    head_revision,
    history,
    is_uninitialised,
    migration_status,
    owned_table_names,
    upgrade,
    upgrade_sql,
)

EXPECTED_TABLES = {
    "dash_cohort",
    "dash_cohort_def",
    "dash_cohort_def_map",
    "indicator",
    "measure",
    "measure_relationship",
    "measure_temporal_window",
    "phenotype",
    "phenotype_definition",
    "query_rule",
    "report",
    "report_cohort_map",
    "report_indicator_map",
    "subquery",
    "subquery_rule_map",
}


@pytest.fixture
def console() -> Console:
    # width pinned so wrapping cannot change what a substring assertion sees
    return Console(width=200, force_terminal=False, no_color=True)


# --------------------------------------------------------------------------
# Scoping
# --------------------------------------------------------------------------


def test_owned_tables_are_exactly_the_dashboard_schema():
    assert owned_table_names() == EXPECTED_TABLES


def test_owned_tables_exclude_the_shared_cdm_metadata():
    """Base.metadata is shared with omop_alchemy and carries the whole CDM."""
    names = owned_table_names()

    assert "person" not in names
    assert "concept" not in names
    # The scoping is only meaningful if the CDM really is on the same metadata.
    assert len(Base.metadata.tables) > len(names)


def test_owned_tables_come_only_from_the_query_package():
    """Guards the scope against widening to all of ``oa_cohorts``.

    The classes in ``oa_cohorts.measurables`` map the CDM materialised views
    owned by omop_constructs. They live in the CDM database and are created by
    another package, so a broader module prefix would wrongly claim them —
    which is what a ``*_mv`` table appearing here would mean.

    Asserted through the mapper registry rather than by importing the
    measurable modules, because importing those opens a connection to the
    configured CDM and this test must not need one.
    """
    owned = owned_table_names()

    assert not any(name.endswith("_mv") for name in owned)
    for mapper in Base.registry.mappers:
        model = mapper.class_
        table = getattr(model, "__table__", None)
        if isinstance(table, sa.Table) and table.name in owned:
            assert model.__module__.startswith("oa_cohorts.query"), (
                f"{model.__name__} contributes owned table {table.name!r} "
                f"but is declared in {model.__module__}"
            )


def test_migrations_do_not_create_cdm_tables(sqlite_engine):
    """Scoping has to hold through Alembic, not just through the comparison."""
    bootstrap_query_schema(sqlite_engine)
    present = set(sa.inspect(sqlite_engine).get_table_names())

    assert "person" not in present
    assert "concept" not in present
    assert present >= EXPECTED_TABLES


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------


def test_migrated_schema_reports_no_differences(sqlite_engine):
    """A schema built by the migrations must compare clean.

    This is the test that keeps the guard usable: any difference reported on a
    correct schema would block every write command forever.
    """
    bootstrap_query_schema(sqlite_engine)
    result = check_schema(sqlite_engine)

    assert result.is_clean, [str(c) for c in result.sorted_changes()]


def test_create_all_schema_also_reports_no_differences(sqlite_engine):
    """The two ways of building the schema must agree with each other."""
    create_owned_tables(sqlite_engine)
    result = check_schema(sqlite_engine)

    assert result.is_clean, [str(c) for c in result.sorted_changes()]


def test_missing_column_is_reported(sqlite_engine):
    bootstrap_query_schema(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("ALTER TABLE report DROP COLUMN report_owner"))

    result = check_schema(sqlite_engine)

    assert not result.is_clean
    assert result.is_blocking
    assert any(c.column == "report_owner" and c.operation == "add_column" for c in result.changes)


def test_missing_table_is_reported(sqlite_engine):
    bootstrap_query_schema(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("DROP TABLE measure_temporal_window"))

    result = check_schema(sqlite_engine)

    assert "measure_temporal_window" in result.tables_missing
    assert any(c.operation == "add_table" for c in result.changes)


def test_empty_database_is_recognised_as_uninitialised(sqlite_engine):
    result = check_schema(sqlite_engine)

    assert is_uninitialised(result)
    assert set(result.tables_missing) == EXPECTED_TABLES


def test_cdm_tables_in_the_database_are_ignored(sqlite_engine):
    """The dashboard database legitimately shares space with other schemas."""
    bootstrap_query_schema(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE person (person_id INTEGER PRIMARY KEY)"))

    assert check_schema(sqlite_engine).is_clean


# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------


def test_packaged_baseline_builds_the_dashboard_schema(sqlite_engine):
    result = bootstrap_query_schema(sqlite_engine)

    assert result.action == "created"
    assert set(result.created_tables) == EXPECTED_TABLES
    assert current_revision(sqlite_engine) == head_revision()


def test_bootstrap_adopts_an_existing_database_without_ddl(sqlite_engine):
    """The stated baseline requirement: current schema becomes revision one."""
    create_owned_tables(sqlite_engine)
    assert current_revision(sqlite_engine) is None

    result = bootstrap_query_schema(sqlite_engine)

    assert result.action == "stamped"
    assert result.created_tables == ()
    assert current_revision(sqlite_engine) == head_revision()


def test_bootstrap_refuses_to_adopt_a_drifted_database(sqlite_engine):
    """Stamping a drifted schema as the baseline would bake the drift in."""
    create_owned_tables(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("ALTER TABLE report DROP COLUMN report_owner"))

    result = bootstrap_query_schema(sqlite_engine)

    assert result.blocked
    assert current_revision(sqlite_engine) is None
    assert result.check is not None and not result.check.is_clean


def test_bootstrap_adopts_a_drifted_database_when_forced(sqlite_engine):
    create_owned_tables(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("ALTER TABLE report DROP COLUMN report_owner"))

    result = bootstrap_query_schema(sqlite_engine, adopt_on_drift=True)

    assert result.action == "stamped"
    assert current_revision(sqlite_engine) == head_revision()


def test_bootstrap_is_idempotent(sqlite_engine):
    bootstrap_query_schema(sqlite_engine)
    result = bootstrap_query_schema(sqlite_engine)

    assert result.action == "unchanged"
    assert migration_status(sqlite_engine).is_up_to_date


def test_status_reports_an_unmanaged_database(sqlite_engine):
    create_owned_tables(sqlite_engine)
    status = migration_status(sqlite_engine)

    assert not status.is_stamped
    assert status.head == head_revision()


def test_history_lists_every_revision_oldest_first():
    revisions = history()

    assert [r.revision for r in revisions] == [
        "0001_baseline",
        "0002_report_date_columns",
        "0003_indicator_map_overrides",
    ]
    assert revisions[0].down_revision is None
    assert not revisions[0].is_head
    assert revisions[-1].is_head
    assert revisions[-1].down_revision == "0002_report_date_columns"


def test_revision_ids_fit_the_alembic_version_column():
    """``alembic_version.version_num`` is VARCHAR(32).

    A longer id applies its DDL and then fails writing the version row, but only on a
    backend that enforces varchar length: SQLite truncates nothing and accepts it, so the
    failure appears first on PostgreSQL, after the schema has already changed. Cheaper to
    assert here.
    """
    too_long = {r.revision: len(r.revision) for r in history() if len(r.revision) > 32}

    assert not too_long, f"revision ids exceed alembic_version.version_num: {too_long}"


# --------------------------------------------------------------------------
# 0003_indicator_map_overrides
# --------------------------------------------------------------------------

#: The columns 0003 adds, with the width each inherits from the indicator column it
#: overrides.
OVERRIDE_COLUMNS = {
    "indicator_label_override": 250,
    "indicator_reference_override": 100,
    "benchmark_override": None,
    "benchmark_unit_override": 20,
}


def _link_columns(engine: sa.Engine) -> dict[str, dict]:
    return {c["name"]: c for c in sa.inspect(engine).get_columns("report_indicator_map")}


def _seed_one_link(engine: sa.Engine) -> None:
    """Insert the rows a report-indicator link needs, as a pre-0003 database would hold them."""
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "insert into measure (measure_id, name, combination, person_ep_override) "
                "values (0, 'Full report cohort', 'rule_or', 0)"
            )
        )
        connection.execute(
            sa.text(
                "insert into indicator "
                "(indicator_id, indicator_description, numerator_measure_id, denominator_measure_id) "
                "values (1, 'Presented at MDT meeting', 0, 0)"
            )
        )
        connection.execute(
            sa.text(
                "insert into report (report_id, report_name, report_short_name, report_description, "
                "report_create_date, report_edit_date, report_author) "
                "values (1, 'Lung Cancer MDT', 'lung_mdt', 'd', '2024-05-15', '2024-05-15', 'GK')"
            )
        )
        connection.execute(
            sa.text("insert into report_indicator_map (report_id, indicator_id) values (1, 1)")
        )


def test_override_columns_arrive_only_with_the_revision(sqlite_engine):
    upgrade(sqlite_engine, "0002_report_date_columns")

    assert not OVERRIDE_COLUMNS.keys() & _link_columns(sqlite_engine).keys()

    upgrade(sqlite_engine, "head")
    columns = _link_columns(sqlite_engine)

    assert OVERRIDE_COLUMNS.keys() <= columns.keys()
    for name, width in OVERRIDE_COLUMNS.items():
        assert columns[name]["nullable"], f"{name} must be nullable so NULL can mean inherit"
        if width is not None:
            assert columns[name]["type"].length == width


def test_existing_links_inherit_after_the_revision(sqlite_engine):
    """Applying 0003 must change no rendered output: every existing link inherits."""
    upgrade(sqlite_engine, "0002_report_date_columns")
    _seed_one_link(sqlite_engine)

    upgrade(sqlite_engine, "head")

    with sqlite_engine.connect() as connection:
        row = connection.execute(sa.text("select * from report_indicator_map")).mappings().one()

    assert [row[name] for name in OVERRIDE_COLUMNS] == [None] * len(OVERRIDE_COLUMNS)


def test_overrides_round_trip(sqlite_engine):
    upgrade(sqlite_engine, "head")
    _seed_one_link(sqlite_engine)

    with sqlite_engine.begin() as connection:
        connection.execute(
            sa.text(
                "update report_indicator_map set "
                "indicator_label_override = 'Presented at Lung MDT meeting', "
                "indicator_reference_override = 'LUCAP 3.1', "
                "benchmark_override = 85, "
                "benchmark_unit_override = 'percent'"
            )
        )

    with sqlite_engine.connect() as connection:
        row = connection.execute(sa.text("select * from report_indicator_map")).mappings().one()

    assert row["indicator_label_override"] == "Presented at Lung MDT meeting"
    assert row["indicator_reference_override"] == "LUCAP 3.1"
    assert row["benchmark_override"] == 85
    assert row["benchmark_unit_override"] == "percent"


def test_revision_downgrades_and_keeps_the_link(sqlite_engine):
    """The columns go; the link they hung off does not."""
    upgrade(sqlite_engine, "head")
    _seed_one_link(sqlite_engine)

    downgrade(sqlite_engine, "0002_report_date_columns")

    assert not OVERRIDE_COLUMNS.keys() & _link_columns(sqlite_engine).keys()
    with sqlite_engine.connect() as connection:
        assert connection.execute(sa.text("select * from report_indicator_map")).all() == [(1, 1)]


def test_migrated_schema_is_clean_at_the_new_head(sqlite_engine):
    upgrade(sqlite_engine, "head")
    result = check_schema(sqlite_engine)

    assert current_revision(sqlite_engine) == head_revision()
    assert result.is_clean, [str(c) for c in result.sorted_changes()]


def test_the_revision_renders_offline(sqlite_engine):
    """``copy_from`` exists so ``schema sql`` works without reflecting the live table."""
    upgrade(sqlite_engine, "0002_report_date_columns")

    sql = upgrade_sql(sqlite_engine, revision="head")

    for name in OVERRIDE_COLUMNS:
        assert f"ADD COLUMN {name}" in sql


def test_upgrade_sql_emits_ddl_without_touching_the_database(sqlite_engine):
    sql = upgrade_sql(sqlite_engine)

    assert "CREATE TABLE report" in sql
    assert "CREATE TABLE person" not in sql
    assert sa.inspect(sqlite_engine).get_table_names() == []


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------


def test_guard_warns_but_does_not_block_a_read(sqlite_engine, console, capsys):
    create_owned_tables(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("ALTER TABLE report DROP COLUMN report_owner"))

    guard_schema(console, sqlite_engine, mode=GuardMode.read)

    assert "Schema Drift" in capsys.readouterr().out


def test_guard_blocks_a_write(sqlite_engine, console, capsys):
    create_owned_tables(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("ALTER TABLE report DROP COLUMN report_owner"))

    with pytest.raises(typer.Exit) as excinfo:
        guard_schema(console, sqlite_engine, mode=GuardMode.write)

    assert excinfo.value.exit_code == 1
    assert "Command Blocked" in capsys.readouterr().out


def test_guard_override_lets_a_write_through(sqlite_engine, console, capsys):
    create_owned_tables(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("ALTER TABLE report DROP COLUMN report_owner"))

    guard_schema(console, sqlite_engine, mode=GuardMode.write, ignore_drift=True)

    assert "Override Active" in capsys.readouterr().out


def test_guard_is_silent_on_a_clean_schema(sqlite_engine, console, capsys):
    """The whole policy rests on this: no noise when nothing is wrong."""
    bootstrap_query_schema(sqlite_engine)

    guard_schema(console, sqlite_engine, mode=GuardMode.write)

    assert capsys.readouterr().out == ""


def test_guard_off_does_nothing(sqlite_engine, console, capsys):
    guard_schema(console, sqlite_engine, mode=GuardMode.off)

    assert capsys.readouterr().out == ""


def test_uninitialised_database_gets_a_bootstrap_hint(sqlite_engine, console, capsys):
    guard_schema(console, sqlite_engine, mode=GuardMode.read)

    assert "Schema Not Initialised" in capsys.readouterr().out


def test_auto_bootstrap_initialises_an_empty_database(sqlite_engine, console, capsys):
    """import-config with --create-tables initialises rather than blocks."""
    result = guard_schema(console, sqlite_engine, mode=GuardMode.write, auto_bootstrap=True)

    assert "Initialising Schema" in capsys.readouterr().out
    assert current_revision(sqlite_engine) == head_revision()
    assert result is not None and result.is_clean


def test_auto_bootstrap_does_not_touch_a_database_that_has_tables(sqlite_engine, console):
    """Only a wholly empty database is initialised; a partial one is drift."""
    create_owned_tables(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("DROP TABLE measure_temporal_window"))

    with pytest.raises(typer.Exit):
        guard_schema(console, sqlite_engine, mode=GuardMode.write, auto_bootstrap=True)

    assert current_revision(sqlite_engine) is None


def test_write_to_an_empty_database_blocks_without_auto_bootstrap(sqlite_engine, console):
    """--no-create-tables means the caller opted out of schema creation."""
    with pytest.raises(typer.Exit):
        guard_schema(console, sqlite_engine, mode=GuardMode.write, auto_bootstrap=False)


def test_failure_is_explained_as_drift(sqlite_engine, console, capsys):
    bootstrap_query_schema(sqlite_engine)
    with sqlite_engine.begin() as connection:
        connection.execute(sa.text("ALTER TABLE report DROP COLUMN report_owner"))

    exc = sa.exc.OperationalError("SELECT report_owner FROM report", {}, Exception("no such column"))

    assert explain_schema_failure(console, sqlite_engine, exc)
    assert "Likely cause: schema drift" in capsys.readouterr().out


def test_failure_is_not_explained_when_the_schema_is_clean(sqlite_engine, console, capsys):
    bootstrap_query_schema(sqlite_engine)

    exc = sa.exc.OperationalError("SELECT 1", {}, Exception("connection reset"))

    assert not explain_schema_failure(console, sqlite_engine, exc)
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------
# The known gap
# --------------------------------------------------------------------------


def test_enum_label_changes_are_not_detected(sqlite_engine):
    """Documents a real limitation rather than asserting correctness.

    Alembic's autogenerate compares column types but never the value set of an
    existing enum, so a label added to a Python enum produces no operation and
    fails at insert time. Enum changes need their ALTER TYPE written by hand.
    If this test ever starts failing, alembic gained the capability and
    docs/schema_management.md should be updated.
    """
    from oa_cohorts.query import Measure

    bootstrap_query_schema(sqlite_engine)
    column_type = Measure.__table__.c.combination.type
    original = list(column_type.enums)
    try:
        column_type.enums = [*original, "rule_nand"]
        assert check_schema(sqlite_engine).is_clean
    finally:
        column_type.enums = original
