from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from oa_cohorts.core import RuleMatcher, RuleTarget, RuleTemporality, ThresholdDirection
from oa_cohorts.measurables.measurable_base import (
    MeasurableBase,
    MeasurableDomain,
    MeasurableSpec,
)
from oa_cohorts import errors as errors_module
from oa_cohorts.query.measure import MeasureExecutor, MeasureSQLCompiler, MissingMaterializedViewError
from tests.db_error_helpers import fake_undefined_table_error
from oa_cohorts.query.query_rule import ScalarRule
from oa_cohorts.query.subquery import Subquery

referral_events = sa.table(
    "referral_events",
    sa.column("person_id"),
    sa.column("episode_id"),
    sa.column("initial_gp_referral"),
    sa.column("referral_to_specialist"),
)


class ReferralWindowMeasurable(MeasurableBase):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.tx,
        label="Referral window",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="initial_gp_referral",
        value_numeric_attr="referral_to_specialist",
    )

    person_id = referral_events.c.person_id
    episode_id = referral_events.c.episode_id
    initial_gp_referral = referral_events.c.initial_gp_referral
    referral_to_specialist = referral_events.c.referral_to_specialist

def test_measure_zero_cannot_execute(db, measure_zero):

    executor = MeasureExecutor(db)

    with pytest.raises(RuntimeError, match="FULL COHORT"):
        executor.execute(measure_zero)

def test_measure_members_without_execution(db, measure_leaf):

    executor = MeasureExecutor(db)

    with pytest.raises(RuntimeError, match="has not been executed"):
        executor.members(measure_leaf)


def test_measure_execute_raises_missing_materialized_view_error(db, measure_leaf, monkeypatch):
    def raise_undefined_table(stmt):
        raise fake_undefined_table_error("primary_diagnosis_condition_mv")

    monkeypatch.setattr(db, "execute", raise_undefined_table)
    monkeypatch.setattr(
        errors_module, "_classify_relation", lambda name: MissingMaterializedViewError
    )

    executor = MeasureExecutor(db)

    with pytest.raises(MissingMaterializedViewError, match="primary_diagnosis_condition_mv") as exc_info:
        executor.execute(measure_leaf)

    assert exc_info.value.relation_name == "primary_diagnosis_condition_mv"
    assert "leaf" in exc_info.value.context


def test_measure_execute_reraises_undefined_table_for_unknown_relation(db, measure_leaf, monkeypatch):
    def raise_undefined_table(stmt):
        raise fake_undefined_table_error("some_other_table")

    monkeypatch.setattr(db, "execute", raise_undefined_table)
    monkeypatch.setattr(errors_module, "_classify_relation", lambda name: None)

    executor = MeasureExecutor(db)

    with pytest.raises(sa.exc.ProgrammingError):
        executor.execute(measure_leaf)


def test_measure_executor_force_refresh(db, measure_leaf):
    executor = MeasureExecutor(db)

    rows1 = executor.execute(measure_leaf)
    rows2 = executor.execute(measure_leaf, force_refresh=True)

    assert rows1 is not rows2

def test_measure_without_subquery_or_children_fails(measure_empty):

    compiler = MeasureSQLCompiler(measure_empty)

    with pytest.raises(ValueError, match="no subquery and no children"):
        compiler.sql_any()

def test_measure_sql_normalise_missing_columns(measure_with_bad_child):

    compiler = MeasureSQLCompiler(measure_with_bad_child)

    with pytest.raises(ValueError, match="missing columns"):
        compiler.sql_any()

def test_invalid_combination_sql(measure_invalid_combination):

    compiler = MeasureSQLCompiler(measure_invalid_combination)

    with pytest.raises(KeyError):
        compiler.sql_any()

def test_subquery_without_rules_fails(subquery_empty):
    with pytest.raises(ValueError, match="has no rules"):
        subquery_empty.sql_any()

def test_subquery_unknown_target(subquery_unknown_target):
    with pytest.raises(KeyError, match="No measurable registered"):
        subquery_unknown_target.sql_any()

def test_subquery_missing_numeric_column(subquery_scalar_rule):
    with pytest.raises(ValueError, match="does not expose a numeric value column"):
        subquery_scalar_rule.sql_any()


def test_referral_window_subquery_exposes_numeric_column(patch_measurable_registry):
    patch_measurable_registry[RuleTarget.referral_to_specialist_window] = ReferralWindowMeasurable

    subquery = Subquery(
        subquery_id=106,
        name="GP referral to first specialist seen < 14d",
        short_name="first_spec",
        target=RuleTarget.referral_to_specialist_window,
        temporality=RuleTemporality.dt_consult,
        rules=[
            ScalarRule(
                query_rule_id=143,
                matcher=RuleMatcher.scalar,
                concept_id=0,
                scalar_threshold=14,
                threshold_direction=ThresholdDirection.lt,
                threshold_comparator=RuleTarget.referral_to_specialist_window,
            )
        ],
    )

    sql = subquery.sql_any()

    assert sql is not None


def test_referral_window_scalar_concept_filter_requires_concept_column(patch_measurable_registry):
    patch_measurable_registry[RuleTarget.referral_to_specialist_window] = ReferralWindowMeasurable

    rule = ScalarRule(
        query_rule_id=144,
        matcher=RuleMatcher.scalar,
        concept_id=4139715,
        scalar_threshold=14,
        threshold_direction=ThresholdDirection.lt,
        threshold_comparator=RuleTarget.referral_to_specialist_window,
    )
    rule.concept = SimpleNamespace(concept_id=4139715, concept_name="Seen by clinical oncologist")

    subquery = Subquery(
        subquery_id=107,
        name="Referral window with concept filter",
        short_name="first_spec_concept",
        target=RuleTarget.referral_to_specialist_window,
        temporality=RuleTemporality.dt_consult,
        rules=[rule],
    )

    with pytest.raises(ValueError, match="scalar concept filtering requires value_concept_attr when concept_id != 0"):
        subquery.sql_any()
