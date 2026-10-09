"""Allow a temporal window to use a filtered measure as its anchor.

Existing rows have NULL anchor_measure_id and retain their subquery anchors.
Remove measure-anchor configuration before downgrading; it cannot be represented
by the previous schema. The frozen table supports SQLite and offline SQL.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "0004_window_measure_anchor"
down_revision = "0003_indicator_map_overrides"
branch_labels = None
depends_on = None


def _frozen_table(*, with_anchor: bool) -> sa.Table:
    columns = [
        sa.Column("measure_id", sa.Integer(), primary_key=True),
        sa.Column("candidate_measure_id", sa.Integer(), nullable=False),
        sa.Column("window_min_days", sa.Integer()),
        sa.Column("window_max_days", sa.Integer()),
        sa.Column("window_pick_strategy", sa.Enum("any", "earliest", "latest", "closest", name="windowpickstrategy")),
        sa.Column("result_date_source", sa.Enum("anchor", "candidate", "greatest", "least", name="resultdatesource")),
        sa.Column("require_same_resolver", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["measure_id"], ["measure.measure_id"], name="fk_measure_temporal_window_measure_id_measure"),
        sa.ForeignKeyConstraint(["candidate_measure_id"], ["measure.measure_id"], name="fk_measure_temporal_window_candidate_measure_id_measure"),
    ]
    if with_anchor:
        columns.extend([
            sa.Column("anchor_measure_id", sa.Integer()),
            sa.ForeignKeyConstraint(["anchor_measure_id"], ["measure.measure_id"], name="fk_measure_temporal_window_anchor_measure_id_measure"),
        ])
    return sa.Table("measure_temporal_window", sa.MetaData(), *columns)


def upgrade() -> None:
    with op.batch_alter_table("measure_temporal_window", copy_from=_frozen_table(with_anchor=False)) as batch:
        batch.add_column(sa.Column("anchor_measure_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_measure_temporal_window_anchor_measure_id_measure", "measure", ["anchor_measure_id"], ["measure_id"])


def downgrade() -> None:
    if not context.is_offline_mode():
        count = op.get_bind().scalar(sa.text("SELECT count(*) FROM measure_temporal_window WHERE anchor_measure_id IS NOT NULL"))
        if count:
            raise ValueError("Remove measure-anchor configuration before downgrading")
    with op.batch_alter_table("measure_temporal_window", copy_from=_frozen_table(with_anchor=True)) as batch:
        batch.drop_constraint("fk_measure_temporal_window_anchor_measure_id_measure", type_="foreignkey")
        batch.drop_column("anchor_measure_id")
