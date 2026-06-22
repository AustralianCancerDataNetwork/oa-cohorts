from omop_constructs.alchemy.episodes import (
    SurgicalProcedureMV, 
    DxTreatStartMV, 
    ConditionTreatmentEpisode, 
    TreatmentEnvelopeMV, 
    ConditionTreatmentIntentMV, 
    ConsultWindowMV
)
from oa_configurator import get_logger
from orm_loader.helpers import Base
from .measurable_base import MeasurableSpec, MeasurableBase, MeasurableDomain
from ..core import RuleTemporality

logger = get_logger(__name__)

class SurgicalMeasurable(SurgicalProcedureMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.tx,
        label="Cancer Surgical Procedures",
        person_id_attr="person_id",
        episode_id_attr="condition_episode_id",
        event_date_attr="surgery_datetime",
        value_concept_attr="surgery_concept_id",
        value_string_attr="surgery_name"
    )

class AllCurrentTreatmentMeasurable(DxTreatStartMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.tx,
        label="Current Condition Treatment",
        person_id_attr="person_id",
        episode_id_attr="dx_episode_id",
        event_date_attr="treatment_start",
        value_concept_attr="treatment_regimen_count",
    )

class ChemoTreatmentMeasurable(ConditionTreatmentEpisode, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.tx,
        label="Chemotherapy Treatment Episodes",
        person_id_attr="person_id",
        episode_id_attr="condition_episode_id",
        event_date_attr="regimen_start_date",
        value_concept_attr="regimen_number",   
        value_string_attr="regimen_concept"
    )

class RTTreatmentMeasurable(ConditionTreatmentEpisode, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.tx,
        label="Radiotherapy Treatment Episodes",
        person_id_attr="person_id",
        episode_id_attr="condition_episode_id",
        event_date_attr="course_start_date",
        value_concept_attr="course_count",
        value_string_attr="course_concept" 
    )

class IntentChemoMeasurable(ConditionTreatmentIntentMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.tx,
        label="Specified Intent of Chemotherapy Treatment Episodes",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="treatment_episode_start_date",
        value_concept_attr="treatment_intent_concept_id",   
        value_predicate_attr="sact"
    )

class IntentRTMeasurable(ConditionTreatmentIntentMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.tx,
        label="Specified Intent of Radiotherapy Treatment Episodes",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="treatment_episode_start_date",
        value_concept_attr="treatment_intent_concept_id",
        value_predicate_attr="rt",
        temporality_map={
            RuleTemporality.dt_rad:           "treatment_episode_start_date",
            RuleTemporality.dt_treatment_end: "treatment_episode_end_date",
        },
    )

class TxDaysBeforeDeath(TreatmentEnvelopeMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.tx,
        label="Treatment Days Before Death",
        person_id_attr="person_id",
        episode_id_attr="condition_episode",
        event_date_attr="condition_start_date",        
        value_concept_attr="condition_episode",  
        value_numeric_attr="treatment_days_before_death"
    )

class TxDaysToStartTreatment(TreatmentEnvelopeMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.tx,
        label="Treatment Days To Start Treatment",
        person_id_attr="person_id",
        episode_id_attr="condition_episode",
        event_date_attr="condition_start_date",
        value_concept_attr="condition_episode",  
        value_numeric_attr="days_from_dx_to_treatment"
    )


class TxConcurrentChemoRT(TreatmentEnvelopeMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.tx,
        label="Concurrent Chemotherapy and Radiotherapy",
        person_id_attr="person_id",
        episode_id_attr="condition_episode",
        event_date_attr="condition_start_date",        
        value_concept_attr="condition_episode",  
        value_predicate_attr="concurrent_chemort"
    )

class ReferralToSpecialistWindowMeasurable(ConsultWindowMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.tx,
        label="Referral to Specialist Window",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="initial_gp_referral",        
        value_numeric_attr="referral_to_specialist"
    )
