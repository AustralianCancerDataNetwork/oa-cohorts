"""Recorded death evidence, attached to the cancers in the demography view."""
import sqlalchemy as sa
from omop_alchemy.cdm.model.clinical import Death
from omop_constructs.alchemy.demography import PersonDemography

from .measurable_base import MeasurableBase, MeasurableDomain, MeasurableSpec


class DeathMeasurable(MeasurableBase):
    # The released demographic view exposes only death_datetime, which cannot
    # distinguish no death record from a record whose timestamp is missing.
    # Join the indexed death table once; use its date when no timestamp exists.
    __table__ = sa.select(
        PersonDemography.person_id, PersonDemography.episode_id,
        Death.person_id.label("death_record_person_id"),
        sa.func.coalesce(sa.func.date(Death.death_datetime), Death.death_date, type_=sa.Date)
        .label("death_date"),
    ).select_from(PersonDemography.__table__.outerjoin(
        Death.__table__, Death.person_id == PersonDemography.person_id,
    )).subquery()
    person_id, episode_id, death_record_person_id, death_date = __table__.c
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.person,
        label="Death",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="death_date",
        value_concept_attr="death_record_person_id",
        source_table="death",
        event_id_attr="death_record_person_id",
    )
