from typing import Any, Protocol, TypeAlias

import sqlalchemy as sa
from sqlalchemy.engine import Row as SARow
from sqlalchemy.sql import CompoundSelect, Select

from ..core import RuleCombination

Row = SARow[Any]

SQLQuery: TypeAlias = Select | CompoundSelect

COMBINATION_SQL = {
    RuleCombination.rule_or: sa.union_all,
    RuleCombination.rule_and: sa.intersect_all,
    RuleCombination.rule_except: sa.except_all,
}

def _first_member_query(query: SQLQuery) -> SQLQuery:
    """Collapse dated members by the complete key after their predicates run."""
    members = query.subquery()
    return sa.select(
        members.c.person_id,
        members.c.episode_id,
        members.c.measure_resolver,
        sa.func.min(members.c.measure_date).label("measure_date"),
    ).group_by(members.c.person_id, members.c.episode_id, members.c.measure_resolver)


class PersonFilter(Protocol):
    """
    A pluggable person-level report filter to hold metadata for cross-tabulation.
    Must return a SQLAlchemy selectable with at least person_id.
    """
    def to_subquery(self) -> sa.Subquery:
        ...