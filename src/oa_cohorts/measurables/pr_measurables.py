"""Recorded death evidence, attached to the cancers in the demography view."""
import sqlalchemy as sa
from omop_alchemy.cdm.model.clinical import Death, Person
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


def _age_in_completed_years() -> sa.ColumnElement[int]:
    """Whole years from birth to the cancer episode start.

    Uses the person's full birth date (``birth_datetime``, else ``month_of_birth``
    and ``day_of_birth``) to subtract a year when the episode starts before that
    year's birthday. With only ``year_of_birth`` recorded the result is the plain
    year difference, so it can read one year high for an episode that starts
    before the birthday.
    """
    start = PersonDemography.episode_start_date
    birth_year = sa.func.coalesce(sa.extract("year", Person.birth_datetime), PersonDemography.year_of_birth)
    birth_month = sa.func.coalesce(sa.extract("month", Person.birth_datetime), Person.month_of_birth)
    birth_day = sa.func.coalesce(sa.extract("day", Person.birth_datetime), Person.day_of_birth, 1)
    start_month, start_day = sa.extract("month", start), sa.extract("day", start)
    before_birthday = sa.case(
        (sa.or_(start_month < birth_month,
                sa.and_(start_month == birth_month, start_day < birth_day)), 1),
        else_=0,
    )
    return sa.cast(sa.extract("year", start) - birth_year - before_birthday, sa.Integer)


class AgeAtDiagnosisMeasurable(MeasurableBase):
    """Age in completed years at the start of each cancer episode.

    Numeric only: select it with a threshold-only ``scalar`` rule (``concept_id``
    0, ``threshold_comparator`` ``demog_age_at_dx``), e.g. ``> 75``. There is no
    concept column, so concept matchers and presence/absence are rejected.
    """
    __table__ = sa.select(
        PersonDemography.person_id, PersonDemography.episode_id, PersonDemography.episode_start_date,
        _age_in_completed_years().label("age_at_diagnosis"),
    ).select_from(PersonDemography.__table__.outerjoin(
        Person.__table__, Person.person_id == PersonDemography.person_id,
    )).subquery()
    person_id, episode_id, episode_start_date, age_at_diagnosis = __table__.c
    __measurable__ = MeasurableSpec(
        domain=MeasurableDomain.person,
        label="Age at diagnosis",
        person_id_attr="person_id",
        episode_id_attr="episode_id",
        event_date_attr="episode_start_date",
        value_numeric_attr="age_at_diagnosis",
    )
