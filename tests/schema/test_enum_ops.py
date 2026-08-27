"""The idempotent enum helper, and the baseline's use of it.

The Postgres behaviour of ``ALTER TYPE ... ADD VALUE IF NOT EXISTS`` cannot be
exercised on SQLite. What is covered here is that the helper stays out of the
way on a backend without native enums, that it emits the right statements when
it does run, and that the baseline's frozen label sets agree with the enum
columns in the same revision.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from oa_cohorts.schema.enum_ops import sync_enum_labels
from oa_cohorts.schema.runner import MIGRATIONS_PATH

BASELINE = MIGRATIONS_PATH / "versions" / "0001_baseline.py"


def _baseline_source() -> str:
    return Path(BASELINE).read_text()


def _declared_enum_labels(source: str) -> dict[str, list[str]]:
    """Label sets as the sa.Enum(...) column declarations state them."""
    declared: dict[str, list[str]] = {}
    for match in re.finditer(r"sa\.Enum\((.*?), name='([a-z_]+)'\)", source):
        declared[match.group(2)] = re.findall(r"'([^']*)'", match.group(1))
    return declared


def _frozen_enum_labels(source: str) -> dict[str, tuple[str, ...]]:
    """The revision's own _ENUM_LABELS snapshot."""
    namespace: dict[str, object] = {}
    block = source[source.index("_ENUM_LABELS") : source.index("def upgrade()")]
    exec(compile(block, str(BASELINE), "exec"), namespace)  # noqa: S102
    return namespace["_ENUM_LABELS"]  # type: ignore[return-value]


def test_baseline_frozen_labels_match_its_own_enum_columns():
    """The sync call and the create_table calls must describe the same thing.

    If they drift apart, a fresh database gets one label set and the sync
    statement asks for another.
    """
    source = _baseline_source()

    assert _frozen_enum_labels(source) == {
        name: tuple(labels) for name, labels in _declared_enum_labels(source).items()
    }


def test_baseline_covers_every_enum_column():
    source = _baseline_source()

    # Seven distinct types across eight enum columns; ruletarget is used twice.
    assert len(_declared_enum_labels(source)) == 7
    assert source.count("sa.Enum(") == 8


def test_sync_enum_labels_is_a_noop_off_postgres(sqlite_engine):
    """SQLite stores these columns as VARCHAR, so there is nothing to alter."""
    with sqlite_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            sync_enum_labels("rulecombination", ("rule_or", "rule_nand"))

    # Reaching here without an error is the assertion: a real ALTER TYPE would
    # have raised on SQLite.
    assert sqlite_engine.dialect.name == "sqlite"


def _offline_sql(*, enum_name: str, labels: tuple[str, ...], schema: str | None = None) -> str:
    """Render what the helper would emit against Postgres, without a server."""
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    with Operations.context(context):
        sync_enum_labels(enum_name, labels, schema=schema)
    return buffer.getvalue()


def test_sync_enum_labels_emits_one_idempotent_statement_per_label():
    """``IF NOT EXISTS`` is what makes the call safe to re-run, and safe to
    leave in a revision that also creates the type."""
    sql = _offline_sql(enum_name="ruletemporality", labels=("dt_any", "dt_referral"))

    assert sql.count("ADD VALUE IF NOT EXISTS") == 2
    assert "ALTER TYPE ruletemporality ADD VALUE IF NOT EXISTS 'dt_any'" in sql
    assert "ALTER TYPE ruletemporality ADD VALUE IF NOT EXISTS 'dt_referral'" in sql


def test_sync_enum_labels_qualifies_the_type_when_given_a_schema():
    sql = _offline_sql(enum_name="ruletarget", labels=("dx_any",), schema="dashboard")

    assert "ALTER TYPE dashboard.ruletarget" in sql


def test_sync_enum_labels_escapes_quotes_in_a_label():
    """A label is interpolated into the statement, so quoting has to hold."""
    sql = _offline_sql(enum_name="weird", labels=("it's",))

    assert "'it''s'" in sql
