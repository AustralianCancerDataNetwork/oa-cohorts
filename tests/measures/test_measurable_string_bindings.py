from __future__ import annotations

import importlib
import sys
import types
import warnings

import pytest
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
        sa.Column("metastatic_disease_id", sa.Integer),
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
        sa.Column("stage_id", sa.Integer, primary_key=True),
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
        sa.Column("value_as_concept_id", sa.Integer),
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
        sa.Column("value_concept_id", sa.Integer),
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
        sa.Column("intent_measurement_id", sa.Integer),
        sa.Column("treatment_episode_id", sa.Integer),
        sa.Column("treatment_episode_start_date", sa.Date),
        sa.Column("treatment_episode_end_date", sa.Date),
        sa.Column("treatment_intent_concept_id", sa.Integer),
        sa.Column("sact", sa.Boolean),
        sa.Column("rt", sa.Boolean),
        sa.Column("concurrent_rt", sa.Boolean),
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
        sa.Column("first_concurrent_rt_start", sa.Date),
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
        sa.Column("episode_start_date", sa.Date),
        sa.Column("year_of_birth", sa.Integer),
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
            for module_name in (
                "oa_cohorts.measurables.dx_measurables",
                "oa_cohorts.measurables.ev_measureables",
                "oa_cohorts.measurables.tx_measurables",
                "oa_cohorts.measurables.pr_measurables",
            )
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


@pytest.mark.parametrize("date_role", ["dt_any", "dt_concurrent"])
def test_concurrent_crt_indicator_window_uses_the_rt_course_date(monkeypatch, date_role):
    from datetime import date

    from oa_cohorts.core import RuleTarget, RuleTemporality
    from oa_cohorts.query.measure import MeasureMember
    from oa_cohorts.query.query_rule import PredicateRule
    from oa_cohorts.query.subquery import Subquery
    from tests.resolution.candidate_runtime import indicator_results

    modules = _load_measurable_modules(monkeypatch)
    measurable = modules["oa_cohorts.measurables.tx_measurables"].TxConcurrentChemoRT
    monkeypatch.setattr("oa_cohorts.query.subquery.get_measurable_registry",
                        lambda: {RuleTarget.tx_concurrent: measurable})
    query = Subquery(subquery_id=82, target=RuleTarget.tx_concurrent, temporality=RuleTemporality(date_role),
                     rules=[PredicateRule(query_rule_id=110, concept_id=1)])
    with sa.create_engine("sqlite://").connect() as db:
        table = measurable.__table__
        table.create(db)
        db.execute(table.insert(), [
            {"mv_id": 1, "person_id": 1, "condition_episode": 10, "condition_start_date": date(2026, 1, 1),
             "concurrent_chemort": True, "first_concurrent_rt_start": date(2026, 3, 2)},
            {"mv_id": 2, "person_id": 2, "condition_episode": 20, "condition_start_date": date(2026, 1, 1),
             "concurrent_chemort": True, "first_concurrent_rt_start": date(2026, 2, 15)},
        ])
        numerator = [MeasureMember.from_row(row) for row in db.execute(query.select())]
    cohort = [MeasureMember(p, e, e, date(2026, 1, 1)) for p, e in [(1, 10), (2, 20)]]
    results = indicator_results(cohort, numerator, cohort,
                                grain="episode", numerator_max_days_prior=0, numerator_max_days_post=45)
    assert {row.denominator.episode_id: row.numerator_value for row in results} == {10: False, 20: True}
    assert next(row.numerator_date for row in results if row.numerator_value) == date(2026, 2, 15)


def test_observation_value_target_binds_the_answer_column(monkeypatch):
    from oa_cohorts.core import RuleTarget
    from oa_cohorts.measurables.measurable_resolver import get_measurable_registry

    modules = _load_measurable_modules(monkeypatch)
    ev = modules["oa_cohorts.measurables.ev_measureables"]
    registry = get_measurable_registry()
    measurable = registry[RuleTarget.obs_value_concept]

    assert measurable is ev.ObserveValueConceptMeasurable
    assert measurable.__bound_measurable__.value_concept_col is measurable.value_concept_id
    assert measurable.__bound_measurable__.context_concept_col is measurable.event_concept_id
    assert registry[RuleTarget.obs_concept].__bound_measurable__.value_concept_col is ev.ObserveMeasurable.event_concept_id
    assert measurable.__measurable__.event_date_attr == ev.ObserveMeasurable.__measurable__.event_date_attr


def test_observation_value_exact_rule_matches_the_answer_on_postgres(monkeypatch):
    import os
    from datetime import date

    from omop_alchemy.cdm.model import Concept

    from oa_cohorts.core import RuleTarget, RuleTemporality
    from oa_cohorts.measurables.measurable_resolver import get_measurable_registry
    from oa_cohorts.query.query_rule import ExactRule, ObservationContextRule
    from oa_cohorts.query.subquery import Subquery

    url = os.environ.get("ENGINE_CDM")
    if not url:
        pytest.skip("Observation value evidence check requires local synthetic PostgreSQL")
    parsed = sa.engine.make_url(url)
    assert parsed.host in ("localhost", "127.0.0.1") and parsed.port == 55433 and parsed.database == "postgres" and not parsed.query

    _load_measurable_modules(monkeypatch)
    registry = get_measurable_registry()
    monkeypatch.setattr("oa_cohorts.query.subquery.get_measurable_registry", lambda: registry)
    day = date(2026, 1, 2)
    with sa.create_engine(parsed).connect() as connection:
        connection.exec_driver_sql(
            "CREATE TEMP TABLE dx_observation_mv (person_id int, episode_id int, event_date date, "
            "event_concept_id int, value_concept_id int)"
        )
        connection.exec_driver_sql(
            "INSERT INTO dx_observation_mv VALUES (1,10,%s,43054909,45881517), "
            "(2,20,%s,43054909,45883458), (2,20,%s,12345,45881517), "
            "(3,30,%s,12345,45883458)", (day, day, day, day)
        )
        for target, expected in (
            (RuleTarget.obs_value_concept, [(1, 10, day)]),
            (RuleTarget.obs_concept, []),
        ):
            rules = [ExactRule(concept_id=45881517, concept=Concept(concept_id=45881517))]
            if target is RuleTarget.obs_value_concept:
                rules.append(ObservationContextRule(concept_id=43054909, concept=Concept(concept_id=43054909)))
            subquery = Subquery(
                subquery_id=1,
                target=target,
                temporality=RuleTemporality.dt_obs,
                rules=rules,
            )
            for statement in (subquery.select(), subquery.get_subquery_evidence()):
                rows = connection.execute(statement).all()
                assert [(row.person_id, row.episode_id, row.measure_date) for row in rows] == expected

            if target is RuleTarget.obs_value_concept:
                subquery.rules.append(ExactRule(concept_id=45883458, concept=Concept(concept_id=45883458)))
                # The guard applies to every answer branch of the evidence UNION.
                evidence = subquery.get_subquery_evidence().subquery()
                assert connection.execute(
                    sa.select(evidence.c.person_id).order_by(evidence.c.person_id)
                ).scalars().all() == [1, 2]


@pytest.mark.parametrize(("target", "guards", "answers", "error"), [
    ("obs_value_concept", 0, 1, "exactly one obs_context"),
    ("obs_value_concept", 2, 1, "exactly one obs_context"),
    ("obs_value_concept", 1, 0, "at least one answer rule"),
    ("obs_concept", 1, 1, "only valid for obs_value_concept"),
    ("meas_value_concept", 1, 1, "only valid for obs_value_concept"),
])
def test_observation_context_rules_reject_invalid_configuration(monkeypatch, target, guards, answers, error):
    from oa_cohorts.core import RuleTarget, RuleTemporality
    from oa_cohorts.measurables.measurable_resolver import get_measurable_registry
    from oa_cohorts.query.query_rule import ExactRule, ObservationContextRule
    from oa_cohorts.query.subquery import Subquery

    _load_measurable_modules(monkeypatch)
    registry = get_measurable_registry()
    monkeypatch.setattr("oa_cohorts.query.subquery.get_measurable_registry", lambda: registry)
    subquery = Subquery(
        subquery_id=1,
        target=RuleTarget(target),
        temporality=RuleTemporality.dt_obs,
        rules=[ObservationContextRule(concept_id=43054909) for _ in range(guards)]
        + [ExactRule(concept_id=45881517) for _ in range(answers)],
    )

    for compile_query in (subquery.select, subquery.get_subquery_evidence):
        with pytest.raises(ValueError, match=error):
            compile_query()


@pytest.mark.parametrize("grain", ["episode", "patient"])
@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_death_uses_record_presence_and_date_fallback(monkeypatch, grain, dialect):
    import os
    from datetime import date

    from oa_cohorts.core import RuleTarget, RuleTemporality
    from oa_cohorts.query.measure import MeasureMember
    from oa_cohorts.query.query_rule import PresenceRule
    from oa_cohorts.query.subquery import Subquery
    from tests.resolution.candidate_runtime import indicator_results

    modules = _load_measurable_modules(monkeypatch)
    death = modules["oa_cohorts.measurables.pr_measurables"].DeathMeasurable
    monkeypatch.setattr("oa_cohorts.query.subquery.get_measurable_registry", lambda: {RuleTarget.demog_death: death})
    query = Subquery(subquery_id=81, target=RuleTarget.demog_death, temporality=RuleTemporality.dt_death,
                     rules=[PresenceRule(query_rule_id=109, concept_id=0)])
    url = "sqlite://" if dialect == "sqlite" else os.environ.get("ENGINE_CDM")
    if not url:
        pytest.skip("PostgreSQL death evidence check requires the local synthetic database")
    parsed = sa.engine.make_url(url)
    if dialect == "postgresql":
        assert parsed.host in ("localhost", "127.0.0.1") and parsed.port == 55433 and parsed.database == "postgres" and not parsed.query
    engine_options = {
        "schema_translate_map": {"primary": None, "vocab": None}
    } if dialect == "sqlite" else {}
    with sa.create_engine(url, execution_options=engine_options).connect() as db:
        db.exec_driver_sql("CREATE TEMPORARY TABLE person_demography_mv (person_id int, episode_id int, gender_concept_id int)")
        db.exec_driver_sql("CREATE TABLE death (person_id int PRIMARY KEY, death_date date, death_datetime timestamp)")
        # Patient 1 has two cancers. Patient 2 has gender but no death record.
        # Patient 3 has date-only death; patient 4 has a genuinely undated record.
        db.exec_driver_sql("INSERT INTO person_demography_mv VALUES (1,10,8507),(1,20,8507),(2,30,8507),(3,40,NULL),(4,50,8507)")
        db.exec_driver_sql("INSERT INTO death VALUES (1,'2026-01-02','2026-01-03 12:00:00'),(3,'2026-01-04',NULL),(4,NULL,NULL)")
        statement = query.select() if dialect == "sqlite" else query.get_subquery_evidence()
        den = [MeasureMember.from_row(row) for row in db.execute(statement)]
        if dialect == "postgresql":
            assert {m.source_event_id for m in den} == {1, 3, 4}
            assert all(m.source_table == "death" and m.date_required for m in den)
        assert {(m.person_id, m.episode_id, m.measure_date) for m in den} == {
            (1, 10, date(2026, 1, 3)), (1, 20, date(2026, 1, 3)),
            (3, 40, date(2026, 1, 4)), (4, 50, None)}
        cohort = [MeasureMember(p, e, e, date(2026, 1, 1)) for p, e in [(1,10),(1,20),(2,30),(3,40),(4,50)]]
        num = [MeasureMember(1, 20, 20, date(2026, 1, 2))]
        rows = indicator_results(den, num, cohort, grain=grain,
                                 numerator_reference="denominator", numerator_max_days_prior=30, numerator_max_days_post=0)
        assert all(r.denominator.person_id != 2 for r in rows)
        assert sum(r.eligibility_status == "unresolved" for r in rows) == 1
        assert sum(r.numerator_value for r in rows) == 1
        assert sum(r.denominator_value for r in rows) == (3 if grain == "episode" else 2)
        passing = next(r for r in rows if r.numerator_value)
        assert passing.passing_denominator.episode_id == 20


def test_absence_rule_marks_only_its_own_alternative_as_membership_only(monkeypatch):
    from oa_cohorts.core import RuleTarget, RuleTemporality
    from oa_cohorts.query.query_rule import AbsenceRule, PresenceRule
    from oa_cohorts.query.subquery import Subquery
    from tests.conftest import ExecutableMeasurable

    monkeypatch.setattr("oa_cohorts.query.subquery.get_measurable_registry", lambda: {RuleTarget.dx_any: ExecutableMeasurable})
    query = Subquery(subquery_id=1, target=RuleTarget.dx_any, temporality=RuleTemporality.dt_any,
                     rules=[AbsenceRule(query_rule_id=1, concept_id=0), PresenceRule(query_rule_id=2, concept_id=0)])
    evidence = query.get_subquery_evidence().subquery()
    with sa.create_engine("sqlite://").connect() as db:
        db.exec_driver_sql("CREATE TABLE test_events (person_id int,episode_id int,event_date date,concept_id int,value_number int)")
        db.exec_driver_sql("INSERT INTO test_events VALUES (1,10,NULL,NULL,NULL),(1,20,NULL,1,NULL)")
        assert list(db.execute(sa.select(evidence.c.episode_id, evidence.c.date_required))) == [(10, False), (20, True)]


def test_age_at_diagnosis_binds_a_numeric_column_only(monkeypatch):
    from oa_cohorts.core import RuleTarget
    from oa_cohorts.measurables.measurable_resolver import get_measurable_registry

    modules = _load_measurable_modules(monkeypatch)
    age = get_measurable_registry()[RuleTarget.demog_age_at_dx]
    bound = age.__bound_measurable__

    assert age is modules["oa_cohorts.measurables.pr_measurables"].AgeAtDiagnosisMeasurable
    assert bound.value_numeric_col is age.age_at_diagnosis
    assert bound.value_concept_col is None
    assert bound.event_date_col is age.episode_start_date


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_age_at_diagnosis_counts_completed_years_and_filters_by_threshold(monkeypatch, dialect):
    import os

    from oa_cohorts.core import RuleTarget, RuleTemporality, ThresholdDirection
    from oa_cohorts.query.measure import MeasureMember
    from oa_cohorts.query.query_rule import ScalarRule
    from oa_cohorts.query.subquery import Subquery

    modules = _load_measurable_modules(monkeypatch)
    age = modules["oa_cohorts.measurables.pr_measurables"].AgeAtDiagnosisMeasurable
    for module in ("oa_cohorts.query.subquery", "oa_cohorts.query.query_rule"):
        monkeypatch.setattr(f"{module}.get_measurable_registry", lambda: {RuleTarget.demog_age_at_dx: age})
    url = "sqlite://" if dialect == "sqlite" else os.environ.get("ENGINE_CDM")
    if not url:
        pytest.skip("PostgreSQL age check requires the local synthetic database")
    parsed = sa.engine.make_url(url)
    if dialect == "postgresql":
        assert parsed.host in ("localhost", "127.0.0.1") and parsed.port == 55433 and parsed.database == "postgres" and not parsed.query
    options = {"schema_translate_map": {"primary": None, "vocab": None}} if dialect == "sqlite" else {}
    with sa.create_engine(url, execution_options=options).connect() as db:
        db.exec_driver_sql("CREATE TEMPORARY TABLE person_demography_mv "
                           "(person_id int, episode_id int, episode_start_date date, year_of_birth int)")
        db.exec_driver_sql("CREATE TABLE person (person_id int PRIMARY KEY, year_of_birth int, "
                           "month_of_birth int, day_of_birth int, birth_datetime timestamp)")
        # 1: born 1950-06-15, episode the day before the 76th birthday -> 75
        # 2: same birth date, episode on the 76th birthday              -> 76
        # 3: month/day columns only (no birth_datetime), 1949-03-01, episode 2026-02-28 -> 76
        # 4: year only (1950), episode 2026-01-10 -> plain year difference 76
        # 5: born 1970, episode 2026 -> 56
        db.exec_driver_sql("INSERT INTO person_demography_mv VALUES "
                           "(1,10,'2026-06-14',1950),(2,20,'2026-06-15',1950),(3,30,'2026-02-28',1949),"
                           "(4,40,'2026-01-10',1950),(5,50,'2026-05-01',1970)")
        db.exec_driver_sql("INSERT INTO person VALUES "
                           "(1,1950,6,15,'1950-06-15 00:00:00'),(2,1950,6,15,'1950-06-15 00:00:00'),"
                           "(3,1949,3,1,NULL),(4,1950,NULL,NULL,NULL),(5,1970,1,1,'1970-01-01 00:00:00')")

        ages = dict(db.execute(sa.select(age.episode_id, age.age_at_diagnosis)).all())
        assert ages == {10: 75, 20: 76, 30: 76, 40: 76, 50: 56}

        over_75 = Subquery(subquery_id=1, target=RuleTarget.demog_age_at_dx, temporality=RuleTemporality.dt_any,
                           rules=[ScalarRule(query_rule_id=1, concept_id=0, scalar_threshold=75,
                                             threshold_direction=ThresholdDirection.gt,
                                             threshold_comparator=RuleTarget.demog_age_at_dx)])
        statement = over_75.select() if dialect == "sqlite" else over_75.get_subquery_evidence()
        members = [MeasureMember.from_row(row) for row in db.execute(statement)]
        assert {m.episode_id for m in members} == {20, 30, 40}


def test_age_at_diagnosis_rejects_concept_rules(monkeypatch):
    from oa_cohorts.core import RuleTarget, RuleTemporality
    from oa_cohorts.query.query_rule import PresenceRule
    from oa_cohorts.query.subquery import Subquery

    modules = _load_measurable_modules(monkeypatch)
    age = modules["oa_cohorts.measurables.pr_measurables"].AgeAtDiagnosisMeasurable
    monkeypatch.setattr("oa_cohorts.query.subquery.get_measurable_registry",
                        lambda: {RuleTarget.demog_age_at_dx: age})
    query = Subquery(subquery_id=1, target=RuleTarget.demog_age_at_dx, temporality=RuleTemporality.dt_any,
                     rules=[PresenceRule(query_rule_id=1, concept_id=0)])
    with pytest.raises(ValueError, match="does not expose required concept value column"):
        query.select()
