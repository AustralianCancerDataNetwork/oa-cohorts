from omop_constructs.alchemy.events import DxMeasurementMV, DxProcedureMV, DxObservationMV, DxRelevantVisitMV
from oa_configurator import get_logger
from orm_loader.helpers import Base
from .measurable_base import MeasurableSpec, MeasurableBase, MeasurableDomain

logger = get_logger(__name__)

class MeasurementMeasurable(DxMeasurementMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.meas,
        label="Diagnosis-episode linked measurements",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="event_date",
        value_concept_attr="event_concept_id",
        value_string_attr="event_label",
        value_numeric_attr="value_as_number"
    )

class ProcedureMeasurable(DxProcedureMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.proc,
        label="Diagnosis-episode linked procedures",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="event_date",
        value_concept_attr="event_concept_id",
        value_string_attr="event_label",
    )

class ObserveMeasurable(DxObservationMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.obs,
        label="Diagnosis-episode linked observations",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="event_date",
        value_concept_attr="event_concept_id",
        value_string_attr="event_label",
        value_numeric_attr="value_as_number"
    )

class VisitSpecialtyMeasurable(DxRelevantVisitMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.visit,
        label="Diagnosis-episode linked specialty visits",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="visit_start_date",
        value_concept_attr="provider_specialty_concept_id",
        value_string_attr="provider_specialty",
    )
