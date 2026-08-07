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

class PersonFilter(Protocol):
    """
    A pluggable person-level report filter to hold metadata for cross-tabulation.
    Must return a SQLAlchemy selectable with at least person_id.
    """
    def to_subquery(self) -> sa.Subquery:
        ...