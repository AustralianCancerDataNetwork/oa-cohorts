from omop_constructs.alchemy.modifiers import (
    ModifiedCondition,
    PrimaryDiagnosisConditionMV,
)
from omop_constructs.alchemy.modifiers.condition_modifier_mv import StageModifier
from orm_loader.helpers import Base

from .measurable_base import MeasurableBase, MeasurableDomain, MeasurableSpec


class AnyConditionMeasurable(ModifiedCondition, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.dx,
        label="Modified Condition",
        person_id_attr="person_id",
        episode_id_attr="condition_episode",
        event_date_attr="condition_start_date",
        value_concept_attr="condition_concept_id",
        value_string_attr="condition_code"
    )

class PrimaryDiagnosisEpisodeMeasurable(PrimaryDiagnosisConditionMV, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.dx,
        label="Primary Diagnosis Episode",
        person_id_attr="person_id",
        episode_id_attr="condition_episode",
        event_date_attr="episode_start_date",
        value_concept_attr="condition_concept_id",
        value_string_attr="condition_code",
    )

class StagedConditionMeasurable(StageModifier, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.dx,
        label="Modified Condition Stage",
        person_id_attr="person_id",
        episode_id_attr="condition_episode",
        event_date_attr="stage_date",
        value_concept_attr="stage_concept_id",
        value_string_attr="stage_label"
    )

class MetsConditionMeasurable(ModifiedCondition, MeasurableBase, Base):
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.dx,
        label="Metastasis Event",
        person_id_attr="person_id",
        episode_id_attr="condition_episode",
        event_date_attr="metastatic_disease_date",
        value_concept_attr="metastatic_disease_concept_id",
        value_string_attr="condition_code"
    )
