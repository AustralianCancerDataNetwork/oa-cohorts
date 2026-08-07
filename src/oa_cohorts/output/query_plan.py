from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, runtime_checkable

import sqlalchemy as sa
from sqlalchemy.sql import CompoundSelect, Select

from ..core.html_utils import HTMLRenderable, RawHTML, esc
from ..query.measure import Measure, MeasureSQLCompiler

SQLQuery: TypeAlias = Select | CompoundSelect

def _render_sql(stmt_or_clause: sa.ClauseElement) -> str:
    """
    Render SQLAlchemy Select / CompoundSelect / ClauseElement to SQL string.
    Prefer Postgres dialect if available.
    """
    try:
        from sqlalchemy.dialects import postgresql
        dialect = postgresql.dialect()
    except ImportError:
        dialect = sa.create_engine("sqlite://").dialect

    compiled = stmt_or_clause.compile(
        dialect=dialect,
        compile_kwargs={"literal_binds": True},
    )
    return str(compiled)

@runtime_checkable
class SupportsSQL(Protocol):
    """
    A node that can produce one or more SQL expressions for preview/execution.
    """
    def sql_any(self, *, ep_override: bool = False) -> SQLQuery: ...
    def sql_first(self, *, ep_override: bool = False) -> SQLQuery: ...
    def sql_undated(self, *, ep_override: bool = False) -> SQLQuery: ...

@dataclass(frozen=True)
class QueryNodeRef:
    """
    Very small "pointer" that lets a QueryPlan be constructed from ORM objects
    without importing those ORM modules here.
    """
    kind: Literal["subquery", "measure"]
    obj: object

class QueryNode(HTMLRenderable):
    """
    Base class for nodes in the logic tree.
    Keep this *tiny*: name/summary + children + optional SQL previews.
    """

    def iter_measures(self) -> Iterable[Measure]:
        if isinstance(self, MeasureNode):
            yield self.measure
        for child in self.children():
            yield from child.iter_measures()

    def iter_nodes(self) -> Iterable[QueryNode]:
        yield self
        for c in self.children():
            yield from c.iter_nodes()

    def title(self) -> str:
        return self.__class__.__name__

    def summary_items(self) -> dict[str, str]:
        return {}

    def children(self) -> Sequence[QueryNode]:
        return ()

    def sql_any(self, *, ep_override: bool = False) -> SQLQuery:
        raise NotImplementedError

    def sql_first(self, *, ep_override: bool = False) -> SQLQuery:
        raise NotImplementedError

    def sql_undated(self, *, ep_override: bool = False) -> SQLQuery:
        raise NotImplementedError

    def _html_title(self) -> str:
        return self.title()

    def _html_header(self) -> dict[str, str]:
        return self.summary_items()

    def _html_inner(self):
        blocks: list[object] = []

        kids = list(self.children())
        if kids:
            blocks.append(RawHTML("<div class='subquery-section-title'>Children</div>"))
            blocks.extend(kids)

        # Optional SQL previews if this node supports it
        if isinstance(self, SupportsSQL):
            blocks.append(RawHTML("<div class='subquery-section-title'>SQL preview</div>"))
            for label, fn in (
                ("ANY", self.sql_any),
                ("FIRST", self.sql_first),
                ("UNDATED", self.sql_undated),
            ):
                try:
                    sql = _render_sql(fn())
                    blocks.append(
                        RawHTML(
                            f"<div style='margin-top:6px;font-weight:bold'>{esc(label)}</div>"
                            f"<pre class='sql-preview'>{esc(sql)}</pre>"
                        )
                    )
                except Exception as e:
                    blocks.append(
                        RawHTML(
                            f"<div style='margin-top:6px;font-weight:bold'>{esc(label)}</div>"
                            f"<div class='sql-error'>SQL preview failed: {esc(e)}</div>"
                        )
                    )

        return blocks

@dataclass
class SubqueryNode(QueryNode):
    """
    Leaf: wraps your ORM Subquery instance.
    """
    subquery: SupportsSQL  

    def title(self) -> str:
        name = getattr(self.subquery, "name", "Subquery")
        return f"Subquery: {name}"

    def summary_items(self) -> dict[str, str]:
        target = getattr(self.subquery, "target", None)
        temporality = getattr(self.subquery, "temporality", None)
        rules = getattr(self.subquery, "rules", None) or []
        return {
            "Target": getattr(target, "value", str(target)) if target is not None else "",
            "Temporality": getattr(temporality, "value", str(temporality)) if temporality is not None else "",
            "Rules": str(len(rules)),
        }

    def sql_any(self, *, ep_override: bool = False) -> SQLQuery:
        return self.subquery.sql_any(ep_override=ep_override)

    def sql_first(self, *, ep_override: bool = False) -> SQLQuery:
        return self.subquery.sql_first(ep_override=ep_override)

    def sql_undated(self, *, ep_override: bool = False) -> SQLQuery:
        return self.subquery.sql_undated(ep_override=ep_override)
    
@dataclass
class MeasureNode(QueryNode):
    measure: Measure

    def title(self) -> str:
        return f"Measure: {self.measure.name}"

    def summary_items(self) -> dict[str, str]:
        return {
            "ID": str(self.measure.measure_id),
            "Op": self.measure.combination.value,
            "Children": str(len(self.measure.children)),
        }

    def children(self) -> Sequence[QueryNode]:
        if self.measure.subquery:
            return [SubqueryNode(self.measure.subquery)]
        return [MeasureNode(m) for m in self.measure.children]

    def sql_any(self, *, ep_override: bool = False) -> SQLQuery:
        return MeasureSQLCompiler(self.measure).sql_any(ep_override=ep_override)

    def sql_first(self, *, ep_override: bool = False) -> SQLQuery:
        return MeasureSQLCompiler(self.measure).sql_first(ep_override=ep_override)

    def sql_undated(self, *, ep_override: bool = False) -> SQLQuery:
        return MeasureSQLCompiler(self.measure).sql_undated(ep_override=ep_override)

@dataclass
class QueryPlan(HTMLRenderable):
    """
    Just a root wrapper, so you can display a whole plan in Jupyter.
    """
    root: QueryNode
    title_text: str = "Query Plan"
    
    def _html_title(self) -> str:
        return self.title_text

    def _html_header(self) -> dict[str, str]:
        return {}

    def _html_inner(self):
        return [self.root]