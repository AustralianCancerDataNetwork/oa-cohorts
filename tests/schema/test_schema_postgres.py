"""The migration machinery against a real PostgreSQL database.

Everything in ``test_schema.py`` runs on SQLite, which is enough for the
scoping and workflow logic but silently skips the parts that only exist on
Postgres: native ``ENUM`` types, ``ALTER TYPE ... ADD VALUE``, and the type
comparison ``compare_type=True`` performs against a real backend.

Production is Postgres, and the two model fixes that motivated this package
(the ``measure_relationship`` primary-key nullability and the redundant
``phenotype_definition`` unique constraint) were both phantom diffs that
*only* appear on a real backend. ``test_check_is_clean_immediately_after_upgrade``
is the test that would have caught them, and the one that catches the next.

Every test here is marked ``requires_database("test_dashboard_db")``:
oa-configurator's pytest plugin skips them when that database is not
configured, and fails them if its connection is not marked ``test_only``.
See ``tests/README.md`` to run them locally.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from oa_cohorts.schema import (
    check_schema,
    create_owned_tables,
    current_revision,
    downgrade,
    head_revision,
    is_uninitialised,
    owned_table_names,
    upgrade,
)
from oa_cohorts.schema.bootstrap import bootstrap_query_schema
from oa_cohorts.schema.enum_ops import sync_enum_labels

pytestmark = pytest.mark.requires_database("test_dashboard_db")

#: Enum types the baseline creates, with the column that uses each. Kept here
#: rather than imported from the revision so a revision that quietly drops a
#: type has to change this file too.
BASELINE_ENUM_TYPES = {
    "resultdatesource",
    "rulecombination",
    "rulematcher",
    "ruletarget",
    "ruletemporality",
    "thresholddirection",
    "windowpickstrategy",
}


def _table_names(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names(schema="public"))


def _enum_labels(engine: sa.Engine, type_name: str) -> list[str]:
    with engine.connect() as connection:
        return list(
            connection.scalars(
                sa.text(
                    """
                    SELECT e.enumlabel
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = :type_name
                    ORDER BY e.enumsortorder
                    """
                ),
                {"type_name": type_name},
            )
        )


def _column_types(engine: sa.Engine, table: str) -> dict[str, str]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                sa.text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = :t AND table_schema = 'public'"
                ),
                {"t": table},
            ).all()
        )


def _enum_type_names(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.scalars(
                sa.text("SELECT typname FROM pg_type WHERE typtype = 'e'")
            )
        )


# --------------------------------------------------------------------------
# Applying the baseline
# --------------------------------------------------------------------------


def test_empty_database_reads_as_uninitialised(pg_engine):
    assert is_uninitialised(check_schema(pg_engine))


def test_baseline_upgrade_creates_every_owned_table(pg_engine):
    upgrade(pg_engine, "head")

    assert owned_table_names() <= _table_names(pg_engine)
    assert current_revision(pg_engine) == head_revision()


def test_baseline_upgrade_creates_native_enum_types(pg_engine):
    """SQLite renders these as VARCHAR + CHECK; only Postgres has the type."""
    upgrade(pg_engine, "head")

    assert BASELINE_ENUM_TYPES <= _enum_type_names(pg_engine)


def test_check_is_clean_immediately_after_upgrade(pg_engine):
    """No phantom drift on the backend that produces it.

    A freshly migrated database must compare equal to the models. Anything
    reported here is a difference Alembic would try to reconcile forever --
    the failure mode that a nullable primary key and a unique constraint
    duplicating a primary key both produced before they were fixed.
    """
    upgrade(pg_engine, "head")

    result = check_schema(pg_engine)

    assert result.is_clean, result.summary_line()


# --------------------------------------------------------------------------
# Adopting a database that predates migration management
# --------------------------------------------------------------------------


def test_bootstrap_adopts_an_existing_schema_without_running_ddl(pg_engine):
    """The path every existing deployment takes exactly once.

    A database built by ``create_all`` before this package existed must be
    adopted by stamping, not by re-running the baseline over live tables.
    """
    create_owned_tables(pg_engine)
    assert current_revision(pg_engine) is None

    result = bootstrap_query_schema(pg_engine)

    assert result.action == "stamped"
    assert result.created_tables == ()
    assert result.revision == head_revision()
    assert current_revision(pg_engine) == head_revision()


def test_an_adopted_schema_then_checks_clean(pg_engine):
    """Adoption is only safe if create_all and the baseline agree.

    If they ever diverge, every adopted database reports drift on its first
    command after upgrading -- so this asserts the two construction paths
    produce the same schema.
    """
    create_owned_tables(pg_engine)
    bootstrap_query_schema(pg_engine)

    result = check_schema(pg_engine)

    assert result.is_clean, result.summary_line()


def test_bootstrap_is_idempotent(pg_engine):
    bootstrap_query_schema(pg_engine)
    second = bootstrap_query_schema(pg_engine)

    assert second.action == "unchanged"
    assert second.revision == head_revision()


# --------------------------------------------------------------------------
# Drift detection
# --------------------------------------------------------------------------


def test_check_detects_a_dropped_column(pg_engine):
    upgrade(pg_engine, "head")
    with pg_engine.begin() as connection:
        connection.execute(sa.text("ALTER TABLE report DROP COLUMN report_owner"))

    result = check_schema(pg_engine)

    assert not result.is_clean
    assert any(
        change.table == "report" and change.column == "report_owner"
        for change in result.sorted_changes()
    ), result.summary_line()


def test_check_ignores_a_table_outside_the_owned_set(pg_engine):
    """The CDM shares Base.metadata; an unrelated table must not read as drift."""
    upgrade(pg_engine, "head")
    with pg_engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE person (person_id integer PRIMARY KEY)"))

    assert check_schema(pg_engine).is_clean


# --------------------------------------------------------------------------
# Enums -- the documented gap, and the tool that closes it
# --------------------------------------------------------------------------


def test_added_enum_label_is_invisible_to_check(pg_engine):
    """Pins the caveat the docs and module docstrings all warn about.

    Autogenerate compares column types but never the value set of an existing
    enum, so this drift is real and undetectable. If a future Alembic starts
    reporting it, this test fails and the warnings can come out.
    """
    upgrade(pg_engine, "head")
    with pg_engine.begin() as connection:
        connection.execute(sa.text("ALTER TYPE rulecombination ADD VALUE 'rule_xor'"))

    assert "rule_xor" in _enum_labels(pg_engine, "rulecombination")
    assert check_schema(pg_engine).is_clean


def test_sync_enum_labels_adds_only_missing_labels(pg_engine):
    """The hand-written half of an enum migration, on the backend it targets."""
    upgrade(pg_engine, "head")
    before = _enum_labels(pg_engine, "rulecombination")

    with pg_engine.begin() as connection:
        _run_in_migration_context(connection, "rulecombination", (*before, "rule_xor"))

    after = _enum_labels(pg_engine, "rulecombination")
    assert after == [*before, "rule_xor"]


def test_sync_enum_labels_is_idempotent(pg_engine):
    """Re-runnable, so a revision touching an enum works on fresh and existing
    databases alike."""
    upgrade(pg_engine, "head")
    labels = (*_enum_labels(pg_engine, "rulecombination"), "rule_xor")

    for _ in range(2):
        with pg_engine.begin() as connection:
            _run_in_migration_context(connection, "rulecombination", labels)

    assert _enum_labels(pg_engine, "rulecombination") == list(labels)


def _run_in_migration_context(
    connection: sa.Connection,
    enum_name: str,
    labels: tuple[str, ...],
) -> None:
    """Call ``sync_enum_labels`` the way a revision does.

    It issues ``alembic.op`` calls, which need an ambient migration context
    rather than a bare connection.
    """
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    with Operations.context(MigrationContext.configure(connection)):
        sync_enum_labels(enum_name, labels)


# --------------------------------------------------------------------------
# Adopting a database older than the models (the 0.8.7 -> 0.9.0 date change)
# --------------------------------------------------------------------------


def _legacy_0_8_7_schema(engine: sa.Engine) -> None:
    """Build the schema a 0.8.7 database has: report dates as TIMESTAMP."""
    upgrade(engine, "0001_baseline")


def test_the_baseline_is_the_0_8_7_shape_not_the_current_models(pg_engine):
    """0001 must describe what is deployed, or nothing can adopt it."""
    _legacy_0_8_7_schema(pg_engine)

    types = _column_types(pg_engine, "report")
    assert types["report_create_date"] == "timestamp without time zone"
    assert types["report_edit_date"] == "timestamp without time zone"


def test_a_pre_0_9_0_database_does_not_match_the_models(pg_engine):
    """The situation that made this revision necessary.

    Without 0002, a 0.8.7 database matches no revision at all and
    `schema bootstrap` refuses it.
    """
    _legacy_0_8_7_schema(pg_engine)

    result = check_schema(pg_engine)

    assert not result.is_clean
    assert {c.column for c in result.sorted_changes()} == {
        "report_create_date",
        "report_edit_date",
    }, result.summary_line()


def test_bootstrap_refuses_a_pre_0_9_0_database_at_head(pg_engine):
    _legacy_0_8_7_schema(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(sa.text("DROP TABLE alembic_version"))

    result = bootstrap_query_schema(pg_engine)

    assert result.action == "blocked"
    # The message has to name the way out, not just the problem.
    assert "--revision 0001_baseline" in " ".join(result.messages)


def test_a_pre_0_9_0_database_adopts_at_the_baseline_then_upgrades_clean(pg_engine):
    """The full documented recovery path, end to end.

    This is the one that matters for deployment: a real 0.8.7 dashboard is
    adopted without DDL, upgraded, and ends up matching the models.
    """
    _legacy_0_8_7_schema(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(sa.text("DROP TABLE alembic_version"))

    adopted = bootstrap_query_schema(pg_engine, revision="0001_baseline")
    assert adopted.action == "stamped"
    assert adopted.revision == "0001_baseline"
    assert adopted.created_tables == ()
    # Still the old shape: adoption runs no DDL.
    assert _column_types(pg_engine, "report")["report_create_date"] == (
        "timestamp without time zone"
    )

    upgrade(pg_engine, "head")

    assert _column_types(pg_engine, "report")["report_create_date"] == "date"
    assert current_revision(pg_engine) == head_revision()
    assert check_schema(pg_engine).is_clean


def test_0002_preserves_the_stored_dates(pg_engine):
    """A type change that silently moved dates would be worse than the drift."""
    _legacy_0_8_7_schema(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO report (report_id, report_name, report_short_name, "
                "report_description, report_create_date, report_edit_date, report_author) "
                "VALUES (1, 'r', 'r', 'd', '2026-03-04 00:00:00', '2026-03-05 00:00:00', 'a')"
            )
        )

    upgrade(pg_engine, "head")

    with pg_engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT report_create_date, report_edit_date FROM report WHERE report_id = 1")
        ).one()
    assert [str(value) for value in row] == ["2026-03-04", "2026-03-05"]


# --------------------------------------------------------------------------
# 0003_indicator_map_overrides
# --------------------------------------------------------------------------

#: Column name -> (data_type, character_maximum_length) it must land as. Lengths are
#: included because they are load-bearing: each override has to hold whatever the
#: indicator column it overrides can hold. ``_column_types`` reports ``data_type`` alone,
#: so this section reads the width itself.
OVERRIDE_COLUMN_TYPES = {
    "indicator_label_override": ("character varying", 250),
    "indicator_reference_override": ("character varying", 100),
    "benchmark_override": ("integer", None),
    "benchmark_unit_override": ("character varying", 20),
}


def _column_types_with_widths(engine: sa.Engine, table: str) -> dict[str, tuple[str, int | None]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT column_name, data_type, character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_name = :t AND table_schema = 'public'"
            ),
            {"t": table},
        ).all()
    return {name: (data_type, width) for name, data_type, width in rows}


def _seed_one_link(engine: sa.Engine) -> None:
    """A report-indicator link as a pre-0003 database would hold it.

    ``rule_or`` rather than ``or``: the combination column is a native enum here, and its
    labels are the enum member names.
    """
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO measure (measure_id, name, combination, person_ep_override) "
                "VALUES (0, 'Full report cohort', 'rule_or', false)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO indicator (indicator_id, indicator_description, "
                "numerator_measure_id, denominator_measure_id) "
                "VALUES (1, 'Presented at MDT meeting', 0, 0)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO report (report_id, report_name, report_short_name, "
                "report_description, report_create_date, report_edit_date, report_author) "
                "VALUES (1, 'Lung Cancer MDT', 'lung_mdt', 'd', '2024-05-15', '2024-05-15', 'GK')"
            )
        )
        connection.execute(
            sa.text("INSERT INTO report_indicator_map (report_id, indicator_id) VALUES (1, 1)")
        )


def test_0003_adds_the_override_columns_with_the_right_types(pg_engine):
    """Widths are load-bearing: each override must hold whatever its canonical column holds."""
    upgrade(pg_engine, "0002_report_date_columns")
    before = _column_types_with_widths(pg_engine, "report_indicator_map")
    assert not OVERRIDE_COLUMN_TYPES.keys() & before.keys()

    upgrade(pg_engine, "head")

    types = _column_types_with_widths(pg_engine, "report_indicator_map")
    assert {name: types[name] for name in OVERRIDE_COLUMN_TYPES} == OVERRIDE_COLUMN_TYPES
    assert current_revision(pg_engine) == head_revision()
    assert check_schema(pg_engine).is_clean


def test_0003_records_its_revision(pg_engine):
    """A revision id longer than ``alembic_version.version_num`` applies its DDL and then
    fails on the version write -- and only here, because SQLite does not enforce the width.
    """
    upgrade(pg_engine, "head")

    with pg_engine.connect() as connection:
        stored = connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()

    assert stored == head_revision()


def test_0003_leaves_existing_links_inheriting(pg_engine):
    upgrade(pg_engine, "0002_report_date_columns")
    _seed_one_link(pg_engine)

    upgrade(pg_engine, "head")

    with pg_engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT * FROM report_indicator_map WHERE report_id = 1")
        ).mappings().one()
    assert [row[name] for name in OVERRIDE_COLUMN_TYPES] == [None] * len(OVERRIDE_COLUMN_TYPES)


def test_0003_downgrade_drops_the_columns_and_keeps_the_link(pg_engine):
    upgrade(pg_engine, "head")
    _seed_one_link(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE report_indicator_map SET indicator_label_override = "
                "'Presented at Lung MDT meeting' WHERE report_id = 1"
            )
        )

    downgrade(pg_engine, "0002_report_date_columns")

    types = _column_types_with_widths(pg_engine, "report_indicator_map")
    assert not OVERRIDE_COLUMN_TYPES.keys() & types.keys()
    with pg_engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT report_id, indicator_id FROM report_indicator_map")
        ).all() == [(1, 1)]
