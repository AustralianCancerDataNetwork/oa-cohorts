from oa_configurator import get_logger
from omop_constructs.alchemy.events import (
    DxMeasurementMV,
    DxObservationMV,
    DxProcedureMV,
    DxRelevantVisitMV,
)
from orm_loader.helpers import Base

from .measurable_base import MeasurableBase, MeasurableDomain, MeasurableSpec

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

class MeasurementValueConceptMeasurable(DxMeasurementMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.meas,
        label="Diagnosis-episode linked measurements (precoordinated answer concept)",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="event_date",
        value_concept_attr="value_as_concept_id",
    )
