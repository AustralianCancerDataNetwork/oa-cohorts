"""report_indicator_map: per-report indicator overrides

An indicator can be linked to more than one report, but everything a report needs to say
about it -- ``indicator_description``, ``indicator_reference``, ``benchmark`` and
``benchmark_unit`` -- lives on the indicator row and is therefore shared by every report
that links to it. A generalisable indicator whose stored text names one tumour stream
carries that stream into reports it does not describe: "Presented at Lung MDT meeting",
cited as LUCAP 3.1 with the lung benchmark, appearing verbatim in the head and neck report.

This revision adds four nullable override columns to the link table so a report can
restate any of those values without forking the indicator. ``NULL`` means "inherit the
canonical value", which is what every existing row gets -- so applying this revision
changes no rendered output on its own.

Who runs it:

* A database at **0002_report_date_columns** needs this applied.
* A database created fresh from the models already has the columns and matches head.
  ``oa-cohorts schema bootstrap`` stamps it here without running any DDL.

Widths mirror the indicator columns each one overrides, so a value that fits the canonical
column cannot fail to fit the override.

``batch_alter_table`` rather than four bare ``add_column`` calls: SQLite implements
``ALTER TABLE ADD COLUMN`` and would manage without it, but ``copy_from`` keeps the
revision renderable in ``--sql`` (offline) mode, where reflecting the live table is
impossible and ``oa-cohorts schema sql`` would otherwise fail. On PostgreSQL batch mode
runs the ALTERs directly.

``copy_from`` carries a full copy of the table, frozen; do not update it to track the
models. Each direction needs the table as it is *before* that direction runs, so there are
two shapes rather than one: ``upgrade`` copies from the pre-override table as ``0001``
created it, and ``downgrade`` copies from the same table plus the four override columns.
Passing the pre-override shape to ``downgrade`` fails with
``KeyError: 'indicator_label_override'`` -- batch mode drops columns from the description
it was given, not from the live table.

``downgrade`` drops the four columns, which discards any override values -- they are
authored by hand and are not reconstructible, so capture them before downgrading.

The revision id is trimmed rather than descriptive: ``alembic_version.version_num`` is
``VARCHAR(32)``, so ``0003_report_indicator_map_overrides`` (35 characters) applies its DDL
and then fails writing the version row on PostgreSQL. SQLite does not enforce varchar
length and accepts it silently, so keep new revision ids inside 32 characters -- there is a
test for this.

Revision ID: 0003_indicator_map_overrides
Revises: 0002_report_date_columns
Create Date: 2026-08-27

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003_indicator_map_overrides'
down_revision: str | None = '0002_report_date_columns'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The columns this revision adds. Held as (name, type) specs rather than as ``Column``
#: objects because a ``Column`` binds to the first table it is added to, and
#: ``Column.copy()`` was removed in SQLAlchemy 2.0 -- so upgrade and downgrade each build
#: their own.
_COLUMN_SPECS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ('indicator_label_override', sa.String(length=250)),
    ('indicator_reference_override', sa.String(length=100)),
    ('benchmark_override', sa.Integer()),
    ('benchmark_unit_override', sa.String(length=20)),
)

def _frozen_table(*, with_overrides: bool) -> sa.Table:
    """``report_indicator_map`` as ``0001_baseline`` creates it, optionally plus the
    override columns this revision adds.

    Frozen; do not update to track the models. A fresh ``MetaData`` per call because a
    ``Table`` binds to the metadata it is created in, and both directions build one.
    """
    columns: list[sa.schema.SchemaItem] = [
        sa.Column('report_id', sa.Integer(), nullable=False),
        sa.Column('indicator_id', sa.Integer(), nullable=False),
    ]
    if with_overrides:
        columns.extend(
            sa.Column(name, column_type, nullable=True) for name, column_type in _COLUMN_SPECS
        )
    columns.extend(
        [
            sa.ForeignKeyConstraint(
                ['indicator_id'],
                ['indicator.indicator_id'],
                name='fk_report_indicator_map_indicator_id_indicator',
            ),
            sa.ForeignKeyConstraint(
                ['report_id'],
                ['report.report_id'],
                name='fk_report_indicator_map_report_id_report',
            ),
            sa.PrimaryKeyConstraint('report_id', 'indicator_id', name='pk_report_indicator_map'),
        ]
    )
    return sa.Table('report_indicator_map', sa.MetaData(), *columns)


def upgrade() -> None:
    with op.batch_alter_table(
        'report_indicator_map', copy_from=_frozen_table(with_overrides=False)
    ) as batch_op:
        for name, column_type in _COLUMN_SPECS:
            batch_op.add_column(sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    """Drop the override columns. Any override values are lost -- see the module docstring."""
    with op.batch_alter_table(
        'report_indicator_map', copy_from=_frozen_table(with_overrides=True)
    ) as batch_op:
        for name, _ in _COLUMN_SPECS:
            batch_op.drop_column(name)
