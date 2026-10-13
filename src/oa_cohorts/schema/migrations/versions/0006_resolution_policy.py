"""Preserve evidence and declare resolution policies without changing clinical opt-ins.

Frozen predecessor tables allow offline SQL and SQLite recreation. Defaults keep
existing windows cohort-relative and select any qualifying opportunity. Undated
numerators now require an explicit presence policy.
"""
from importlib import import_module

import sqlalchemy as sa
from alembic import context, op

revision = "0006_resolution_policy"
down_revision = "0005_indicator_grain"
branch_labels = None
depends_on = None

POLICIES = {
    "measure": [("matching_scope", 9, "cancer", ("cancer", "treatment"))],
    "measure_temporal_window": [
        ("anchor_selection", 6, "first", ("first", "latest", "all")),
        ("anchor_order_date_role", 10, "measure", ("measure", "start", "end", "completion")),
        ("anchor_date_role", 10, "measure", ("measure", "start", "end", "completion")),
        ("candidate_date_role", 10, "measure", ("measure", "start", "end", "completion")),
    ],
    "indicator": [
        ("opportunity_selection", 5, "any", ("any", "first")),
        ("numerator_reference", 11, "cohort", ("cohort", "denominator")),
        ("period_date_source", 11, "denominator", ("cohort", "denominator", "numerator", "completion")),
        ("undated_numerator_policy", 8, "exclude", ("exclude", "presence")),
    ],
}


def constraint_name(table, field):
    prefix = "window" if table == "measure_temporal_window" else table
    return f"ck_{table}_{prefix}_{field}"


def predicate(field, choices):
    literals = ", ".join(repr(choice) for choice in choices)
    return f"{field} IN ({literals})"


def frozen_table(name, *, with_policy):
    package = "oa_cohorts.schema.migrations.versions"
    if name == "indicator":
        table = import_module(f"{package}.0005_indicator_grain")._frozen_table(with_grain=True)
    elif name == "measure_temporal_window":
        table = import_module(f"{package}.0004_window_measure_anchor")._frozen_table(with_anchor=True)
    else:
        table = sa.Table("measure", sa.MetaData(),
            sa.Column("measure_id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(250), nullable=False),
            sa.Column("combination", sa.Enum("rule_or", "rule_and", "rule_except", name="rulecombination"), nullable=False),
            sa.Column("subquery_id", sa.Integer()),
            sa.Column("person_ep_override", sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(["subquery_id"], ["subquery.subquery_id"], name="fk_measure_subquery_id_subquery"))
    if with_policy:
        for field, length, default, choices in POLICIES[name]:
            table.append_column(sa.Column(field, sa.String(length), nullable=False, server_default=default))
            table.append_constraint(sa.CheckConstraint(predicate(field, choices), name=constraint_name(name, field)))
    return table


def upgrade():
    for table, policies in POLICIES.items():
        with op.batch_alter_table(table, copy_from=frozen_table(table, with_policy=False)) as batch:
            for field, length, default, choices in policies:
                batch.add_column(sa.Column(field, sa.String(length), nullable=False, server_default=default))
                batch.create_check_constraint(op.f(constraint_name(table, field)), predicate(field, choices))


def downgrade():
    if not context.is_offline_mode():
        for table, policies in POLICIES.items():
            for field, _, default, _ in policies:
                count = op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {table} WHERE {field} != :default"), {"default": default})
                if count:
                    raise ValueError("Remove nondefault resolution policies before downgrading")
    for table, policies in reversed(POLICIES.items()):
        with op.batch_alter_table(table, copy_from=frozen_table(table, with_policy=True)) as batch:
            for field, _, _, _ in reversed(policies):
                batch.drop_constraint(op.f(constraint_name(table, field)), type_="check")
                batch.drop_column(field)
