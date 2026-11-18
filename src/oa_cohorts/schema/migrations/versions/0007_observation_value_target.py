"""Add the observation answer target and its context matcher."""

import sqlalchemy as sa
from alembic import context, op

from oa_cohorts.schema.enum_ops import sync_enum_labels

revision = "0007_observation_value_target"
down_revision = "0006_resolution_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sync_enum_labels("ruletarget", ("obs_value_concept",))
    sync_enum_labels("rulematcher", ("obs_context",))


def downgrade() -> None:
    # PostgreSQL cannot remove individual enum labels. Retain them after
    # verifying that older versions will not encounter them in config rows.
    if context.is_offline_mode():
        return
    in_use = op.get_bind().scalar(sa.text("""
        SELECT EXISTS (SELECT 1 FROM subquery WHERE CAST(target AS VARCHAR) = 'obs_value_concept')
            OR EXISTS (SELECT 1 FROM query_rule WHERE CAST(threshold_comparator AS VARCHAR) = 'obs_value_concept')
            OR EXISTS (SELECT 1 FROM query_rule WHERE CAST(matcher AS VARCHAR) = 'obs_context')
    """))
    if in_use:
        raise ValueError("Remove observation value rules before downgrading")
