"""Add the age-at-diagnosis rule target."""

import sqlalchemy as sa
from alembic import context, op

from oa_cohorts.schema.enum_ops import sync_enum_labels

revision = "0008_age_at_diagnosis_target"
down_revision = "0007_observation_value_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sync_enum_labels("ruletarget", ("demog_age_at_dx",))


def downgrade() -> None:
    # PostgreSQL cannot remove individual enum labels. Retain the label after
    # verifying that older versions will not encounter it in config rows.
    if context.is_offline_mode():
        return
    in_use = op.get_bind().scalar(sa.text("""
        SELECT EXISTS (SELECT 1 FROM subquery WHERE CAST(target AS VARCHAR) = 'demog_age_at_dx')
            OR EXISTS (SELECT 1 FROM query_rule WHERE CAST(threshold_comparator AS VARCHAR) = 'demog_age_at_dx')
    """))
    if in_use:
        raise ValueError("Remove age-at-diagnosis rules before downgrading")
