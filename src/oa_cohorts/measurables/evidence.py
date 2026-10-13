"""Physical evidence bindings and intrinsic eligibility, independent of rule filters."""
import sqlalchemy as sa


def intrinsic_predicate(cls):
    """Modality and intent qualify on the same physical treatment fact."""
    spec = cls.__bound_measurable__
    conditions = [spec.eligibility_col.is_(True)] if spec.eligibility_col is not None else []
    if spec.reject_conflicting_values:
        columns = spec.evidence_columns or {}
        opportunity = columns["opportunity_id"]
        other = cls.__table__.alias()
        conflict = sa.exists(sa.select(1).select_from(other).where(
            other.c[spec.person_id_col.key] == spec.person_id_col,
            other.c[opportunity.key] == opportunity,
            other.c[spec.value_concept_col.key] != spec.value_concept_col,
        ))
        conditions.append(~conflict)
    return sa.and_(sa.true(), *conditions)

def evidence_selectables(cls, temporality, ep_override=False):
    """Source IDs are optional; never substitute a refresh-generated mv_id."""
    from ..query.evidence import EVIDENCE_TYPES

    spec = cls.__bound_measurable__
    columns = dict(spec.evidence_columns or {})
    for candidate in ("event_id", "condition_occurrence_id", "surgery_occurrence_id", "visit_occurrence_id"):
        if "source_event_id" not in columns and hasattr(cls, candidate):
            columns["source_event_id"] = getattr(cls, candidate)
    event_date = sa.cast(cls.temporal_anchor(temporality), sa.Date)
    columns.update(source_date=event_date, completion_date=event_date,
                   date_required=sa.true(),
                   source_table=sa.literal(spec.source_table or getattr(cls, "__tablename__", None), type_=sa.String))
    return (*cls.filter_table_dated(temporality, ep_override), *(
        columns.get(name, sa.cast(sa.null(), kind)).label(name) for name, kind in EVIDENCE_TYPES.items()))

