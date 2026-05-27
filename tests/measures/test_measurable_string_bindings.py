from __future__ import annotations

import importlib
import sys
import types
import warnings

import sqlalchemy as sa
from orm_loader.helpers import Base
from sqlalchemy.exc import SAWarning

from oa_cohorts.measurables.measurable_base import MeasurableBase

_EXPECTED_STRING_BINDINGS = {
    ("oa_cohorts.measurables.dx_measurables", "AnyConditionMeasurable"): (
        "condition_code",
        "modified_conditions_mv",
    ),
    ("oa_cohorts.measurables.dx_measurables", "PrimaryDiagnosisEpisodeMeasurable"): (
        "condition_code",
        "primary_diagnosis_condition_mv",
    ),
    ("oa_cohorts.measurables.dx_measurables", "StagedConditionMeasurable"): (
        "stage_label",
        "stage_modifier_mv",
    ),
    ("oa_cohorts.measurables.dx_measurables", "MetsConditionMeasurable"): (
        "condition_code",
        "modified_conditions_mv",
    ),
    ("oa_cohorts.measurables.ev_measureables", "MeasurementMeasurable"): (
        "event_label",
        "dx_measurement_mv",
    ),
    ("oa_cohorts.measurables.ev_measureables", "ProcedureMeasurable"): (
        "event_label",
        "dx_procedure_mv",
    ),
    ("oa_cohorts.measurables.ev_measureables", "ObserveMeasurable"): (
        "event_label",
        "dx_observation_mv",
    ),
    ("oa_cohorts.measurables.ev_measureables", "VisitSpecialtyMeasurable"): (
        "provider_specialty",
        "dx_relevant_visit_mv",
    ),
    ("oa_cohorts.measurables.tx_measurables", "SurgicalMeasurable"): (
        "surgery_name",
        "surgical_procedure_mv",
    ),
    ("oa_cohorts.measurables.tx_measurables", "ChemoTreatmentMeasurable"): (
        "regimen_concept",
        "condition_treatment_episode_mv",
    ),
    ("oa_cohorts.measurables.tx_measurables", "RTTreatmentMeasurable"): (
        "course_concept",
        "condition_treatment_episode_mv",
    ),
}


def _fake_mv_class(class_name: str, table: sa.Table) -> type[Base]:
    return type(
        class_name,
        (Base,),
        {
            "__table__": table,
            **{column.name: column for column in table.c},
        },
    )


def _load_measurable_modules(monkeypatch):
    modified_table = sa.Table(
        "modified_conditions_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("condition_episode", sa.Integer),
        sa.Column("condition_start_date", sa.Date),
        sa.Column("condition_concept_id", sa.Integer),
        sa.Column("condition_code", sa.String),
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
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("condition_episode", sa.Integer),
        sa.Column("stage_date", sa.Date),
        sa.Column("stage_concept_id", sa.Integer),
        sa.Column("stage_label", sa.String),
        extend_existing=True,
    )
    measurement_table = sa.Table(
        "dx_measurement_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("episode_id", sa.Integer),
        sa.Column("event_date", sa.Date),
        sa.Column("event_concept_id", sa.Integer),
        sa.Column("event_label", sa.String),
        sa.Column("value_as_number", sa.Numeric),
        extend_existing=True,
    )
    procedure_table = sa.Table(
        "dx_procedure_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("episode_id", sa.Integer),
        sa.Column("event_date", sa.Date),
        sa.Column("event_concept_id", sa.Integer),
        sa.Column("event_label", sa.String),
        extend_existing=True,
    )
    observation_table = sa.Table(
        "dx_observation_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("episode_id", sa.Integer),
        sa.Column("event_date", sa.Date),
        sa.Column("event_concept_id", sa.Integer),
        sa.Column("event_label", sa.String),
        sa.Column("value_as_number", sa.Numeric),
        extend_existing=True,
    )
    visit_table = sa.Table(
        "dx_relevant_visit_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("episode_id", sa.Integer),
        sa.Column("visit_start_date", sa.Date),
        sa.Column("provider_specialty_concept_id", sa.Integer),
        sa.Column("provider_specialty", sa.String),
        extend_existing=True,
    )
    surgical_table = sa.Table(
        "surgical_procedure_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("condition_episode_id", sa.Integer),
        sa.Column("surgery_datetime", sa.Date),
        sa.Column("surgery_concept_id", sa.Integer),
        sa.Column("surgery_name", sa.String),
        extend_existing=True,
    )
    treatment_episode_table = sa.Table(
        "condition_treatment_episode_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("condition_episode_id", sa.Integer),
        sa.Column("regimen_start_date", sa.Date),
        sa.Column("regimen_number", sa.Integer),
        sa.Column("regimen_concept", sa.String),
        sa.Column("course_start_date", sa.Date),
        sa.Column("course_count", sa.Integer),
        sa.Column("course_concept", sa.String),
        extend_existing=True,
    )
    dx_treat_start_table = sa.Table(
        "dx_treat_start_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("dx_episode_id", sa.Integer),
        sa.Column("treatment_start", sa.Date),
        sa.Column("treatment_regimen_count", sa.Integer),
        extend_existing=True,
    )
    treatment_intent_table = sa.Table(
        "condition_treatment_intent_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("episode_id", sa.Integer),
        sa.Column("treatment_episode_start_date", sa.Date),
        sa.Column("treatment_intent_concept_id", sa.Integer),
        sa.Column("sact", sa.Boolean),
        sa.Column("rt", sa.Boolean),
        extend_existing=True,
    )
    treatment_envelope_table = sa.Table(
        "treatment_envelope_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("condition_episode", sa.Integer),
        sa.Column("condition_start_date", sa.Date),
        sa.Column("treatment_days_before_death", sa.Integer),
        sa.Column("days_from_dx_to_treatment", sa.Integer),
        sa.Column("concurrent_chemort", sa.Boolean),
        extend_existing=True,
    )
    consult_window_table = sa.Table(
        "consult_window_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("episode_id", sa.Integer),
        sa.Column("initial_gp_referral", sa.Date),
        sa.Column("referral_to_specialist", sa.Integer),
        extend_existing=True,
    )
    demography_table = sa.Table(
        "person_demography_mv",
        Base.metadata,
        sa.Column("mv_id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer),
        sa.Column("episode_id", sa.Integer),
        sa.Column("death_datetime", sa.Date),
        sa.Column("gender_concept_id", sa.Integer),
        extend_existing=True,
    )

    omop_constructs_pkg = types.ModuleType("omop_constructs")
    omop_constructs_pkg.__path__ = []
    alchemy_pkg = types.ModuleType("omop_constructs.alchemy")
    alchemy_pkg.__path__ = []

    modifiers_mod = types.ModuleType("omop_constructs.alchemy.modifiers")
    modifiers_mod.ModifiedCondition = _fake_mv_class("FakeModifiedCondition", modified_table)
    modifiers_mod.PrimaryDiagnosisConditionMV = _fake_mv_class(
        "FakePrimaryDiagnosisConditionMV", primary_dx_table
    )

    condition_modifier_mod = types.ModuleType(
        "omop_constructs.alchemy.modifiers.condition_modifier_mv"
    )
    condition_modifier_mod.StageModifier = _fake_mv_class("FakeStageModifier", stage_table)

    events_mod = types.ModuleType("omop_constructs.alchemy.events")
    events_mod.DxMeasurementMV = _fake_mv_class("FakeDxMeasurementMV", measurement_table)
    events_mod.DxProcedureMV = _fake_mv_class("FakeDxProcedureMV", procedure_table)
    events_mod.DxObservationMV = _fake_mv_class("FakeDxObservationMV", observation_table)
    events_mod.DxRelevantVisitMV = _fake_mv_class("FakeDxRelevantVisitMV", visit_table)

    episodes_mod = types.ModuleType("omop_constructs.alchemy.episodes")
    episodes_mod.SurgicalProcedureMV = _fake_mv_class(
        "FakeSurgicalProcedureMV", surgical_table
    )
    episodes_mod.DxTreatStartMV = _fake_mv_class("FakeDxTreatStartMV", dx_treat_start_table)
    episodes_mod.ConditionTreatmentEpisode = _fake_mv_class(
        "FakeConditionTreatmentEpisode", treatment_episode_table
    )
    episodes_mod.TreatmentEnvelopeMV = _fake_mv_class(
        "FakeTreatmentEnvelopeMV", treatment_envelope_table
    )
    episodes_mod.ConditionTreatmentIntentMV = _fake_mv_class(
        "FakeConditionTreatmentIntentMV", treatment_intent_table
    )
    episodes_mod.ConsultWindowMV = _fake_mv_class(
        "FakeConsultWindowMV", consult_window_table
    )

    demography_mod = types.ModuleType("omop_constructs.alchemy.demography")
    demography_mod.PersonDemography = _fake_mv_class("FakePersonDemography", demography_table)

    monkeypatch.setitem(sys.modules, "omop_constructs", omop_constructs_pkg)
    monkeypatch.setitem(sys.modules, "omop_constructs.alchemy", alchemy_pkg)
    monkeypatch.setitem(sys.modules, "omop_constructs.alchemy.modifiers", modifiers_mod)
    monkeypatch.setitem(
        sys.modules,
        "omop_constructs.alchemy.modifiers.condition_modifier_mv",
        condition_modifier_mod,
    )
    monkeypatch.setitem(sys.modules, "omop_constructs.alchemy.events", events_mod)
    monkeypatch.setitem(sys.modules, "omop_constructs.alchemy.episodes", episodes_mod)
    monkeypatch.setitem(sys.modules, "omop_constructs.alchemy.demography", demography_mod)

    for module_name in (
        "oa_cohorts.measurables.dx_measurables",
        "oa_cohorts.measurables.ev_measureables",
        "oa_cohorts.measurables.tx_measurables",
        "oa_cohorts.measurables.pr_measurables",
    ):
        sys.modules.pop(module_name, None)

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="This declarative base already contains a class with the same class name",
            category=SAWarning,
        )
        return {
            module_name: importlib.import_module(module_name)
            for module_name in {
                "oa_cohorts.measurables.dx_measurables",
                "oa_cohorts.measurables.ev_measureables",
                "oa_cohorts.measurables.tx_measurables",
                "oa_cohorts.measurables.pr_measurables",
            }
        }


def test_string_mapped_measurables_bind_expected_columns(monkeypatch):
    modules = _load_measurable_modules(monkeypatch)

    actual_bindings = {
        (module.__name__, name): cls
        for module in modules.values()
        for name, cls in vars(module).items()
        if (
            isinstance(cls, type)
            and issubclass(cls, MeasurableBase)
            and getattr(getattr(cls, "__measurable__", None), "value_string_attr", None)
        )
    }

    assert set(actual_bindings) == set(_EXPECTED_STRING_BINDINGS)

    for key, measurable_cls in actual_bindings.items():
        expected_attr, expected_table = _EXPECTED_STRING_BINDINGS[key]
        bound = measurable_cls.__bound_measurable__
        bound_col = bound.value_string_col

        assert measurable_cls.__measurable__.value_string_attr == expected_attr
        assert bound_col is getattr(measurable_cls, expected_attr)
        assert bound_col.key == expected_attr
        assert bound_col.table.name == expected_table
        assert isinstance(bound_col.type, sa.String)
