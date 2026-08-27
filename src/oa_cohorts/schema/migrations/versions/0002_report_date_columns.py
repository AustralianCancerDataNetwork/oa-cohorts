"""report date columns: DateTime -> Date

0.9.0 (commit e7b30de, 2026-08-07) changed ``Report.report_create_date`` and
``Report.report_edit_date`` from ``sa.DateTime`` to ``sa.Date``. Both were
already annotated ``Mapped[date]`` and every stored value was midnight, so the
model change was a correction rather than a semantic shift -- but it never came
with a migration, which is what this revision is.

Who runs it:

* A database created by **0.8.7 or earlier** has TIMESTAMP columns, matches
  ``0001_baseline``, and needs this applied. Adopt with
  ``oa-cohorts schema bootstrap --revision 0001_baseline``, then
  ``oa-cohorts schema upgrade``.
* A database created by **0.9.0** already has DATE columns and matches head.
  ``oa-cohorts schema bootstrap`` stamps it here without running any DDL.

``batch_alter_table`` rather than a bare ``alter_column``: SQLite cannot alter a
column type in place, and the test suite exercises this revision on both
backends. On PostgreSQL batch mode runs the ALTER directly.

``copy_from`` carries a full copy of the table as ``0001`` created it. Without
it, batch mode reflects the live table -- which is impossible in ``--sql``
(offline) mode, so ``oa-cohorts schema sql`` would fail rather than render.
Spelling the table out also keeps the revision self-contained: it describes the
schema it operates on rather than following the models as they move.

Revision ID: 0002_report_date_columns
Revises: 0001_baseline
Create Date: 2026-08-27

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002_report_date_columns'
down_revision: str | None = '0001_baseline'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ('report_create_date', 'report_edit_date')

#: ``report`` exactly as ``0001_baseline`` creates it. Frozen; do not update to
#: track the models.
_REPORT_AT_0001 = sa.Table(
    'report',
    sa.MetaData(),
    sa.Column('report_id', sa.Integer(), nullable=False),
    sa.Column('report_name', sa.String(length=250), nullable=False),
    sa.Column('report_short_name', sa.String(length=50), nullable=False),
    sa.Column('report_description', sa.String(length=1000), nullable=False),
    sa.Column('report_create_date', sa.DateTime(), nullable=False),
    sa.Column('report_edit_date', sa.DateTime(), nullable=False),
    sa.Column('report_author', sa.String(length=250), nullable=False),
    sa.Column('report_owner', sa.String(length=250), nullable=True),
    sa.PrimaryKeyConstraint('report_id', name='pk_report'),
    sa.UniqueConstraint('report_short_name', name='uq_report_report_short_name'),
)


def upgrade() -> None:
    with op.batch_alter_table('report', copy_from=_REPORT_AT_0001) as batch_op:
        for column in _COLUMNS:
            batch_op.alter_column(
                column,
                existing_type=sa.DateTime(),
                type_=sa.Date(),
                existing_nullable=False,
                # Postgres will not implicitly cast timestamp -> date in an
                # ALTER TYPE; the truncation has to be asked for.
                postgresql_using=f'{column}::date',
            )


def downgrade() -> None:
    """Widen back to TIMESTAMP. Values return as midnight, which is what they were."""
    with op.batch_alter_table('report', copy_from=_REPORT_AT_0001) as batch_op:
        for column in _COLUMNS:
            batch_op.alter_column(
                column,
                existing_type=sa.Date(),
                type_=sa.DateTime(),
                existing_nullable=False,
                postgresql_using=f'{column}::timestamp',
            )
