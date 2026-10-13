"""Declare indicator grain; clinical opt-ins are separate guarded configuration.

Frozen indicator shape supports offline SQL and SQLite batch recreation.
Existing rows default to episode. Patient configuration must be explicitly
removed before an online downgrade can discard this field.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "0005_indicator_grain"
down_revision = "0004_window_measure_anchor"
branch_labels = None
depends_on = None


def _frozen_table(*, with_grain: bool) -> sa.Table:
    columns = [
        sa.Column("indicator_id", sa.Integer(), primary_key=True),
        sa.Column("indicator_description", sa.String(250), nullable=False),
        sa.Column("indicator_reference", sa.String(100)),
        sa.Column("numerator_measure_id", sa.Integer(), nullable=False),
        sa.Column("denominator_measure_id", sa.Integer(), nullable=False),
        sa.Column("numerator_max_days_prior", sa.Integer()),
        sa.Column("numerator_max_days_post", sa.Integer()),
        sa.Column("denominator_max_days_prior", sa.Integer()),
        sa.Column("denominator_max_days_post", sa.Integer()),
        sa.Column("benchmark", sa.Integer()),
        sa.Column("benchmark_unit", sa.String(20)),
        sa.ForeignKeyConstraint(["numerator_measure_id"], ["measure.measure_id"], name="fk_indicator_numerator_measure_id_measure"),
        sa.ForeignKeyConstraint(["denominator_measure_id"], ["measure.measure_id"], name="fk_indicator_denominator_measure_id_measure"),
    ]
    if with_grain:
        columns.append(sa.Column("grain", sa.String(7), nullable=False, server_default="episode"))
        columns.append(sa.CheckConstraint("grain IN ('episode', 'patient')", name="ck_indicator_indicator_grain"))
    return sa.Table("indicator", sa.MetaData(), *columns)


def upgrade() -> None:
    with op.batch_alter_table("indicator", copy_from=_frozen_table(with_grain=False)) as batch:
        batch.add_column(sa.Column("grain", sa.String(7), nullable=False, server_default="episode"))
        batch.create_check_constraint(op.f("ck_indicator_indicator_grain"), "grain IN ('episode', 'patient')")


def downgrade() -> None:
    if not context.is_offline_mode():
        count = op.get_bind().scalar(sa.text("SELECT count(*) FROM indicator WHERE grain = 'patient'"))
        if count:
            raise ValueError("Remove patient-grain configuration before downgrading")
    with op.batch_alter_table("indicator", copy_from=_frozen_table(with_grain=True)) as batch:
        batch.drop_constraint(op.f("ck_indicator_indicator_grain"), type_="check")
        batch.drop_column("grain")
