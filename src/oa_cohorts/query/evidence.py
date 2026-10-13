"""Evidence-preserving SQL; canonical membership is a projection of this relation.

Cumulative AND is factorized: each child's evidence is gated by the other
children's grouped membership, rather than materializing their Cartesian product.
Underlying child relations remain reusable by independent temporal consumers.
"""
from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from omop_alchemy.toolkit.episodes.derivation import absolute_day_delta, shift_date
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement

from ..core import ResultDateSource, RuleCombination, WindowPickStrategy
from .typing import SQLQuery

KEY = ("person_id", "episode_id", "measure_resolver")
EVIDENCE_TYPES = {
    "source_table": sa.String(), "source_event_id": sa.BigInteger(),
    "opportunity_id": sa.BigInteger(), "source_date": sa.Date(),
    "start_date": sa.Date(), "end_date": sa.Date(), "completion_date": sa.Date(),
    "anchor_date": sa.Date(), "candidate_date": sa.Date(),
    "anchor_event_id": sa.BigInteger(), "candidate_event_id": sa.BigInteger(),
    "date_required": sa.Boolean(),
}
FIELDS = (*KEY, "measure_date", *EVIDENCE_TYPES)


class LatestDate(FunctionElement):
    """A linear-size maximum expression on PostgreSQL and SQLite."""
    type = sa.Date()
    inherit_cache = True


@compiles(LatestDate)
def compile_latest_date(element, compiler, **kwargs):
    return f"greatest({compiler.process(element.clauses, **kwargs)})"


@compiles(LatestDate, "sqlite")
def compile_sqlite_latest_date(element, compiler, **kwargs):
    return f"max({compiler.process(element.clauses, **kwargs)})"


def evidence_relation(query: SQLQuery) -> sa.Subquery:
    source = query.subquery()
    missing = {*KEY, "measure_date"} - set(source.c.keys())
    if missing:
        raise ValueError(f"Evidence normalisation is missing columns {sorted(missing)}")
    columns = [source.c[name] for name in (*KEY, "measure_date")]
    for name, kind in EVIDENCE_TYPES.items():
        default = source.c.measure_date if name in ("source_date", "completion_date") else sa.cast(sa.null(), kind)
        if name == "date_required":
            default = sa.true()
        columns.append(source.c.get(name, default).label(name))
    return sa.select(*columns).distinct().subquery()


def canonical_query(query: SQLQuery) -> SQLQuery:
    source = query.subquery()
    return sa.select(*(source.c[name] for name in (*KEY, "measure_date"))).distinct()


def matching_key(left, right, *, treatment=False):
    names = (*KEY, "opportunity_id") if treatment else KEY
    return sa.and_(*(left.c[name] == right.c[name] for name in names))


def first_evidence(source, *, selection="first", date_role="measure"):
    date_column = source.c[f"{date_role}_date"]
    if selection == "all":
        return sa.select(*source.c).subquery()
    order = date_column.desc().nulls_last() if selection == "latest" else date_column.asc().nulls_last()
    ranked = sa.select(*source.c, sa.func.row_number().over(
        partition_by=[source.c[name] for name in KEY],
        order_by=[order, source.c.opportunity_id.asc().nulls_last(),
                  source.c.source_table.asc().nulls_last(), source.c.source_event_id.asc().nulls_last()],
    ).label("_rank")).subquery()
    return sa.select(*(ranked.c[name] for name in FIELDS)).where(ranked.c._rank == 1).subquery()


def _completion(dates, required=None):
    """Absence can gate a dated criterion without supplying an event date."""
    if len(dates) == 1:
        return dates[0]
    required = required if required is not None else [sa.true()] * len(dates)
    unknown = sa.or_(*(sa.and_(value.is_(None), needed) for value, needed in zip(dates, required, strict=True)))
    # SQLite's scalar max propagates NULL, unlike PostgreSQL's greatest.
    maximum = LatestDate(*(sa.func.coalesce(value, sa.literal(date.min)) for value in dates))
    return sa.case((sa.or_(unknown, sa.and_(*(value.is_(None) for value in dates))), sa.cast(sa.null(), sa.Date)),
                   else_=maximum)


class EvidenceCompiler:
    def __init__(self, measure):
        self.measure = measure

    def query(self, *, ep_override=False):
        ep_override = ep_override or self.measure.person_ep_override
        if self.measure.is_temporal_window:
            return self.window(ep_override)
        if self.measure.subquery is not None:
            return self.leaf(self.measure.subquery, ep_override)
        parts = [sa.select(evidence_relation(EvidenceCompiler(child).query(ep_override=ep_override))).cte()
                 for child in self.measure.children]
        if not parts:
            raise ValueError(f"Measure {self.measure.measure_id} has no subquery and no children")
        if self.measure.combination == RuleCombination.rule_or:
            return sa.union(*(sa.select(part) for part in parts))
        if self.measure.combination == RuleCombination.rule_except:
            return self.exclusion(parts)
        if self.measure.combination == RuleCombination.rule_and:
            return self.conjunction(parts)
        raise KeyError(self.measure.combination)

    @staticmethod
    def leaf(subquery, ep_override):
        method = getattr(subquery, "get_subquery_evidence", subquery.get_subquery_any)
        return sa.select(evidence_relation(method(ep_override=ep_override)))

    def exclusion(self, parts):
        first, *others = parts
        treatment = getattr(self.measure, "matching_scope", None) == "treatment"
        absent = [~sa.exists(sa.select(1).select_from(part).where(matching_key(first, part, treatment=treatment)))
                  for part in others]
        return sa.select(first).where(*absent)

    def conjunction(self, parts):
        treatment = getattr(self.measure, "matching_scope", None) == "treatment"
        names = (*KEY, "opportunity_id") if treatment else KEY
        minima = [self.first_completion(part, names) for part in parts]
        branches = []
        for index, part in enumerate(parts):
            joined = part
            dates = [part.c.completion_date]
            required = [part.c.date_required]
            for other_index, other in enumerate(minima):
                if index != other_index:
                    joined = joined.join(other, matching_key(part, other, treatment=treatment))
                    dates.append(other.c.first_completion)
                    required.append(other.c.date_required == 1)
            completed = _completion(dates, required)
            overrides = {"measure_date": completed, "completion_date": completed,
                         "date_required": sa.or_(*required)}
            columns = [overrides.get(name, part.c[name]).label(name)
                       for name in FIELDS]
            branches.append(sa.select(*columns).select_from(joined))
        return sa.union(*branches)

    @staticmethod
    def first_completion(part, names):
        required = sa.func.min(sa.case((sa.and_(part.c.completion_date.is_(None),
                                               part.c.date_required.is_(False)), 0), else_=1))
        completed = sa.case((required == 0, sa.cast(sa.null(), sa.Date)),
                            else_=sa.func.min(part.c.completion_date))
        return sa.select(*(part.c[name] for name in names), completed.label("first_completion"),
                         required.label("date_required")).group_by(*(part.c[name] for name in names)).cte()

    def window(self, ep_override):
        cfg = self.measure.window_config
        anchor_measure = getattr(cfg, "anchor_measure", None)
        query = (self.leaf(self.measure.subquery, ep_override) if anchor_measure is None
                 else EvidenceCompiler(anchor_measure).query(ep_override=ep_override))
        anchor = first_evidence(evidence_relation(query),
            selection=getattr(cfg, "anchor_selection", None) or "first",
            date_role=getattr(cfg, "anchor_order_date_role", None) or "measure")
        candidate = evidence_relation(EvidenceCompiler(cfg.candidate_measure).query(ep_override=ep_override))
        anchor_date = anchor.c[f"{getattr(cfg, 'anchor_date_role', None) or 'measure'}_date"]
        candidate_date = candidate.c[f"{getattr(cfg, 'candidate_date_role', None) or 'measure'}_date"]
        join = matching_key(anchor, candidate) if cfg.require_same_resolver else anchor.c.person_id == candidate.c.person_id
        bounds = [anchor_date.is_not(None), candidate_date.is_not(None)]
        for days, operator in ((cfg.window_min_days, "ge"), (cfg.window_max_days, "le")):
            if days is not None:
                limit = shift_date(anchor_date, days=days)
                bounds.append(candidate_date >= limit if operator == "ge" else candidate_date <= limit)
        source = cfg.result_date_source or ResultDateSource.candidate
        emitted = {ResultDateSource.anchor: anchor_date, ResultDateSource.candidate: candidate_date,
                   ResultDateSource.greatest: _completion([anchor_date, candidate_date]),
                   ResultDateSource.least: sa.case((anchor_date < candidate_date, anchor_date), else_=candidate_date)}[source]
        overrides = {
            "person_id": anchor.c.person_id, "episode_id": anchor.c.episode_id,
            "measure_resolver": anchor.c.measure_resolver, "measure_date": emitted,
            "completion_date": _completion([anchor_date, candidate_date,
                                              anchor.c.completion_date, candidate.c.completion_date]),
            "anchor_date": anchor_date, "candidate_date": candidate_date,
            "anchor_event_id": anchor.c.source_event_id, "candidate_event_id": candidate.c.source_event_id,
            "opportunity_id": sa.func.coalesce(anchor.c.opportunity_id, candidate.c.opportunity_id),
            "date_required": sa.true(),
        }
        for name in ("start_date", "end_date"):
            overrides[name] = sa.case((anchor.c.opportunity_id.is_not(None), anchor.c[name]), else_=candidate.c[name])
        base = sa.select(*(overrides.get(name, candidate.c[name]).label(name) for name in FIELDS),
            anchor.c.opportunity_id.label("_anchor_opportunity"),
            anchor.c.source_table.label("_anchor_source_table"),
        ).select_from(anchor.join(candidate, join)).where(*bounds).distinct().subquery()
        return self.pick_candidate(base, cfg.window_pick_strategy or WindowPickStrategy.earliest)

    @staticmethod
    def pick_candidate(base, strategy):
        if strategy == WindowPickStrategy.any:
            return sa.select(*(base.c[name] for name in FIELDS))
        order = [base.c.candidate_date.asc().nulls_last()]
        if strategy == WindowPickStrategy.latest:
            order = [base.c.candidate_date.desc().nulls_last()]
        elif strategy == WindowPickStrategy.closest:
            order.insert(0, absolute_day_delta(base.c.candidate_date, base.c.anchor_date))
        order.extend([base.c.source_table.asc().nulls_last(), base.c.source_event_id.asc().nulls_last()])
        partition = [base.c[name] for name in (*KEY, "anchor_date", "anchor_event_id",
                                              "_anchor_opportunity", "_anchor_source_table")]
        ranked = sa.select(base, sa.func.row_number().over(partition_by=partition, order_by=order).label("_rank")).subquery()
        return sa.select(*(ranked.c[name] for name in FIELDS)).where(ranked.c._rank == 1)
