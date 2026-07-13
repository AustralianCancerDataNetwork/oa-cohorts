from __future__ import annotations

import importlib
import sys
import types

import sqlalchemy as sa
from orm_loader.helpers import Base

from oa_cohorts.core import RuleMatcher, RuleTarget, RuleTemporality
from oa_cohorts.query.query_rule import PresenceRule
from oa_cohorts.query.subquery import Subquery

_stub_counter = 0


def _load_dx_primary_registry(monkeypatch):
    global _stub_counter
    _stub_counter += 1

    modified_table = sa.Table(
        "modified_conditions_mv",
        Base.metadata,
        sa.Column("condition_occurrence_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("condition_concept_id", sa.Integer),
        sa.Column("condition_code", sa.String),
        sa.Column("condition_episode", sa.Integer),
        sa.Column("condition_start_date", sa.Date),
        sa.Column("metastatic_disease_date", sa.Date),
        sa.Column("metastatic_disease_concept_id", sa.Integer),
        extend_existing=True,
    )
    primary_dx_table = sa.Table(
        "primary_diagnosis_condition_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("condition_episode", sa.Integer),
        sa.Column("episode_start_date", sa.Date),
        sa.Column("condition_concept_id", sa.Integer),
        sa.Column("condition_code", sa.String),
        extend_existing=True,
    )
    stage_table = sa.Table(
        "stage_modifier_mv",
        Base.metadata,
        sa.Column("stage_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("condition_episode", sa.Integer),
        sa.Column("stage_date", sa.Date),
        sa.Column("stage_concept_id", sa.Integer),
        sa.Column("stage_label", sa.String),
        extend_existing=True,
    )

    FakeModifiedCondition = type(
        f"FakeModifiedCondition{_stub_counter}",
        (Base,),
        {
            "__table__": modified_table,
            "person_id": modified_table.c.person_id,
            "condition_occurrence_id": modified_table.c.condition_occurrence_id,
            "condition_concept_id": modified_table.c.condition_concept_id,
            "condition_code": modified_table.c.condition_code,
            "condition_episode": modified_table.c.condition_episode,
            "condition_start_date": modified_table.c.condition_start_date,
            "metastatic_disease_date": modified_table.c.metastatic_disease_date,
            "metastatic_disease_concept_id": modified_table.c.metastatic_disease_concept_id,
        },
    )
    FakePrimaryDiagnosisConditionMV = type(
        f"FakePrimaryDiagnosisConditionMV{_stub_counter}",
        (Base,),
        {
            "__table__": primary_dx_table,
            "person_id": primary_dx_table.c.person_id,
            "condition_episode": primary_dx_table.c.condition_episode,
            "episode_start_date": primary_dx_table.c.episode_start_date,
            "condition_concept_id": primary_dx_table.c.condition_concept_id,
            "condition_code": primary_dx_table.c.condition_code,
        },
    )

    FakeStageModifier = type(
        f"FakeStageModifier{_stub_counter}",
        (Base,),
        {
            "__table__": stage_table,
            "person_id": stage_table.c.person_id,
            "condition_episode": stage_table.c.condition_episode,
            "stage_date": stage_table.c.stage_date,
            "stage_concept_id": stage_table.c.stage_concept_id,
            "stage_label": stage_table.c.stage_label,
        },
    )

    omop_constructs_pkg = types.ModuleType("omop_constructs")
    omop_constructs_pkg.__path__ = []
    alchemy_pkg = types.ModuleType("omop_constructs.alchemy")
    alchemy_pkg.__path__ = []
    modifiers_mod = types.ModuleType("omop_constructs.alchemy.modifiers")
    modifiers_mod.ModifiedCondition = FakeModifiedCondition
    modifiers_mod.PrimaryDiagnosisConditionMV = FakePrimaryDiagnosisConditionMV
    condition_modifier_mod = types.ModuleType(
        "omop_constructs.alchemy.modifiers.condition_modifier_mv"
    )
    condition_modifier_mod.StageModifier = FakeStageModifier
    tx_measurables_mod = types.ModuleType("oa_cohorts.measurables.tx_measurables")
    for name in (
        "SurgicalMeasurable",
        "AllCurrentTreatmentMeasurable",
        "ChemoTreatmentMeasurable",
        "RTTreatmentMeasurable",
        "IntentChemoMeasurable",
        "IntentRTMeasurable",
        "IntentConcurrentRTMeasurable",
        "TxDaysBeforeDeath",
        "TxDaysToStartTreatment",
        "TxConcurrentChemoRT",
        "ReferralToSpecialistWindowMeasurable",
    ):
        setattr(tx_measurables_mod, name, type(name, (), {}))
    ev_measurables_mod = types.ModuleType("oa_cohorts.measurables.ev_measureables")
    for name in (
        "MeasurementMeasurable",
        "ProcedureMeasurable",
        "ObserveMeasurable",
        "VisitSpecialtyMeasurable",
        "MeasurementValueConceptMeasurable",
    ):
        setattr(ev_measurables_mod, name, type(name, (), {}))
    pr_measurables_mod = types.ModuleType("oa_cohorts.measurables.pr_measurables")
    pr_measurables_mod.DeathMeasurable = type("DeathMeasurable", (), {})

    monkeypatch.setitem(sys.modules, "omop_constructs", omop_constructs_pkg)
    monkeypatch.setitem(sys.modules, "omop_constructs.alchemy", alchemy_pkg)
    monkeypatch.setitem(sys.modules, "omop_constructs.alchemy.modifiers", modifiers_mod)
    monkeypatch.setitem(
        sys.modules,
        "omop_constructs.alchemy.modifiers.condition_modifier_mv",
        condition_modifier_mod,
    )
    monkeypatch.setitem(sys.modules, "oa_cohorts.measurables.tx_measurables", tx_measurables_mod)
    monkeypatch.setitem(sys.modules, "oa_cohorts.measurables.ev_measureables", ev_measurables_mod)
    monkeypatch.setitem(sys.modules, "oa_cohorts.measurables.pr_measurables", pr_measurables_mod)

    resolver = importlib.import_module("oa_cohorts.measurables.measurable_resolver")
    dx_measurables = importlib.import_module("oa_cohorts.measurables.dx_measurables")

    return resolver.get_measurable_registry(), dx_measurables.PrimaryDiagnosisEpisodeMeasurable


def test_dx_primary_uses_primary_episode_measurable(monkeypatch):
    registry, primary_cls = _load_dx_primary_registry(monkeypatch)

    assert registry[RuleTarget.dx_primary] is primary_cls
    assert registry[RuleTarget.dx_any] is not primary_cls


def test_dx_primary_sql_anchors_to_primary_episode_start(monkeypatch):
    registry, _ = _load_dx_primary_registry(monkeypatch)
    monkeypatch.setattr("oa_cohorts.query.subquery.get_measurable_registry", lambda: registry)

    subquery = Subquery(
        subquery_id=108,
        name="Primary diagnosis",
        short_name="primary_dx",
        target=RuleTarget.dx_primary,
        temporality=RuleTemporality.dt_current_start,
        rules=[
            PresenceRule(
                query_rule_id=145,
                matcher=RuleMatcher.presence,
                concept_id=0,
            )
        ],
    )

    sql = subquery._render_sql(subquery.sql_any())

    assert "episode_start_date AS measure_date" in sql
    assert "condition_start_date AS measure_date" not in sql
    assert "primary_diagnosis_condition_mv" in sql
    assert "overarching_disease_episode_mv" not in sql
