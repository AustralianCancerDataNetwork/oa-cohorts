from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
import sqlalchemy.orm as so
from orm_loader.helpers import Base

from ..core import ResultDateSource, RuleCombination, WindowPickStrategy
from ..core.executability import ExecStatus, MeasureExecCheck
from ..core.html_utils import (
    HTMLChild,
    HTMLRenderable,
    RawHTML,
    esc,
    sql_block,
    table,
    td,
)
from .subquery import Subquery
from .typing import COMBINATION_SQL, SQLQuery


@dataclass(frozen=True)
class MeasureMember:
    """
    Immutable representation of a resolved measure membership event.

    Attributes
    ----------
    person_id:
        Person identifier.
    measure_resolver:
        Logical grouping key (often episode or event id).
    episode_id:
        Optional episode linkage.
    measure_date:
        Optional date associated with the qualifying event.
    """

    person_id: int
    measure_resolver: int
    episode_id: int | None = None
    measure_date: date | None = None

    @classmethod
    def from_row(cls, r):
        return cls(
            person_id=r.person_id,
            measure_resolver=r.measure_resolver,
            episode_id=getattr(r, "episode_id", None),
            measure_date=getattr(r, "measure_date", None),
        )


class Measure(HTMLRenderable, Base):
    """
    Recursive logical unit producing a set of MeasureMember rows.

    A Measure can be:

    - Leaf:
        Backed by a Subquery.
    - Composite:
        Combination of child measures via RuleCombination.

    Semantics
    ---------
    combination:
        Defines how child measures are combined (OR / AND / EXCEPT).

    subquery:
        Atomic SQL-producing unit (optional if children exist).

    Execution Model
    ---------------
    Measures do NOT execute themselves.
    They are executed via MeasureExecutor.

    After execution:
        self._members holds cached MeasureMember rows.

    Special Case
    ------------
    measure_id == 0 represents FULL COHORT.
    It is resolved at Report level and cannot be executed directly.
    """
    __tablename__ = "measure"
    __allow_unmapped__ = True 
    measure_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    id = so.synonym("measure_id")

    name: so.Mapped[str] = so.mapped_column(sa.String(250))
    combination: so.Mapped[RuleCombination] = so.mapped_column(sa.Enum(RuleCombination))

    subquery_id: so.Mapped[int | None] = so.mapped_column(sa.ForeignKey("subquery.subquery_id"), nullable=True)
    person_ep_override: so.Mapped[bool] = so.mapped_column(sa.Boolean, default=False)

    subquery: so.Mapped[Subquery | None] = so.relationship("Subquery", lazy="joined")

    child_links: so.Mapped[list[MeasureRelationship]] = so.relationship(
        "MeasureRelationship",
        foreign_keys="MeasureRelationship.parent_measure_id",
        lazy="selectin",
    )

    parent_links: so.Mapped[list[MeasureRelationship]] = so.relationship(
        "MeasureRelationship",
        foreign_keys="MeasureRelationship.child_measure_id",
        lazy="selectin",
    )

    window_config: so.Mapped[MeasureTemporalWindow | None] = so.relationship(
        "MeasureTemporalWindow",
        foreign_keys="MeasureTemporalWindow.measure_id",
        back_populates="measure",
        uselist=False,
        lazy="joined",
    )

    _members: Sequence[MeasureMember] | None = None

    @property
    def is_temporal_window(self) -> bool:
        return self.window_config is not None

    def members(self, executor: MeasureExecutor):
        return executor.members(self)
    
    @property
    def children(self) -> list[Measure]:
        return [rel.child for rel in self.child_links]
    
    def __repr__(self) -> str:
        header = f"<Measure {self.name!r} op={self.combination.value}>"

        if self.subquery:
            return f"{header}\n  Subquery: {self.subquery.name}"

        if not self.children:
            return header + "\n  (no children)"

        kids = "\n".join(f"  - {c.name} (#{c.measure_id})" for c in self.children)
        return f"{header}\n{kids}"
    

    def is_executable(self) -> MeasureExecCheck:
        """
        Check whether this measure can successfully generate SQL.

        PASS  = all variants compile
        WARN  = at least one compiles, at least one fails
        FAIL  = none compile
        """
        # Special case: measure_id = 0 (full cohort)
        if self.measure_id == 0:
            return MeasureExecCheck(
                status=ExecStatus.PASS,
                ok_variants=["FULL_COHORT"],
                failed_variants={},
            )

        compiler = MeasureSQLCompiler(self)

        checks: list[tuple[str, Callable]] = [
            ("ANY", compiler.sql_any),
            ("FIRST", compiler.sql_first),
            ("UNDATED", compiler.sql_undated),
        ]

        ok: list[str] = []
        failed: dict[str, str] = {}

        for label, fn in checks:
            try:
                stmt = fn()
                # Force compilation (no execution)
                _ = self._render_sql(stmt)
                ok.append(label)
            except Exception as e:
                failed[label] = str(e)

        if ok and not failed:
            status = ExecStatus.PASS
        elif ok and failed:
            status = ExecStatus.WARN
        else:
            status = ExecStatus.FAIL

        return MeasureExecCheck(
            status=status,
            ok_variants=ok,
            failed_variants=failed,
        )

    def _html_css_class(self) -> str:
        return "measure"

    def _html_title(self) -> str:
        return f"Measure: {self.name}"

    def _html_header(self) -> dict[str, object]:
        if self.measure_id == 0:
            return {
                "ID": self.measure_id,
                "Name": self.name,
                "Combination": RawHTML(
                    "<span class='badge neutral'>FULL COHORT</span>"
                ),
                "Subquery": RawHTML("<i>Report cohort (no filtering)</i>"),
                "Children": len(self.children),
                "Episode override": "n/a",
            }
        if self.is_temporal_window:
            cfg = self.window_config
            assert cfg is not None
            min_d = _fmt_days_offset(cfg.window_min_days) if cfg.window_min_days is not None else "open"
            max_d = _fmt_days_offset(cfg.window_max_days) if cfg.window_max_days is not None else "open"
            candidate_name = (
                cfg.candidate_measure.name if cfg.candidate_measure else str(cfg.candidate_measure_id)
            )
            return {
                "ID": self.measure_id,
                "Name": self.name,
                "Kind": RawHTML("<span class='badge temporal_window'>TEMPORAL WINDOW</span>"),
                "Anchor": self.subquery.name if self.subquery else RawHTML("<i>missing</i>"),
                "Candidate": candidate_name,
                "Window": f"{min_d} .. {max_d}",
                "Pick": (cfg.window_pick_strategy or WindowPickStrategy.earliest).value,
                "Result date": (cfg.result_date_source or ResultDateSource.candidate).value,
            }

        return {
            "ID": self.measure_id,
            "Name": self.name,
            "Combination": RawHTML(
                f"<span class='badge {self.combination.value}'>"
                f"{esc(self.combination.value.upper())}</span>"
            ),
            "Subquery": self.subquery.name if self.subquery else "",
            "Children": len(self.children),
            "Episode override": "yes" if self.person_ep_override else "no",
        }

    def _html_inner(self):
        blocks: list[HTMLChild] = []

        if self.measure_id == 0:
            blocks.append(
                RawHTML(
                    "<div class='muted'>"
                    "<i>This measure represents the full report cohort (no filtering applied).</i>"
                    "</div>"
                )
            )
            return blocks

        if self.is_temporal_window:
            cfg = self.window_config
            if self.subquery:
                blocks.append(RawHTML("<div class='subquery-section-title'>Anchor</div>"))
                blocks.append(self.subquery)
            if cfg and cfg.candidate_measure:
                blocks.append(RawHTML("<div class='subquery-section-title'>Candidate</div>"))
                blocks.append(cfg.candidate_measure)
        else:
            # Subquery (leaf)
            if self.subquery:
                blocks.append(RawHTML("<div class='subquery-section-title'>Subquery</div>"))
                blocks.append(self.subquery)

            # Children (composite)
            if self.children:
                blocks.append(RawHTML("<div class='subquery-section-title'>Children</div>"))
                blocks.extend(self.children)
            elif not self.subquery:
                blocks.append(RawHTML("<div class='muted'><i>No children</i></div>"))

        # SQL previews (ANY / FIRST / UNDATED) — shared for all measure kinds
        try:
            compiler = MeasureSQLCompiler(self)

            blocks.append(RawHTML("<div class='subquery-section-title'>SQL preview</div>"))

            for label, fn in (
                ("ANY", compiler.sql_any),
                ("FIRST", compiler.sql_first),
                ("UNDATED", compiler.sql_undated),
            ):
                try:
                    blocks.append(
                        RawHTML(
                            f"<div style='margin-top:6px;font-weight:bold'>{esc(label)}</div>"
                        )
                    )
                    blocks.append(sql_block(fn()))
                except Exception as e:
                    blocks.append(
                        RawHTML(
                            f"<div class='sql-error'>"
                            f"{esc(label)} SQL preview failed: {esc(e)}</div>"
                        )
                    )

        except Exception as e:
            blocks.append(
                RawHTML(
                    f"<div class='sql-error'>SQL preview setup failed: {esc(e)}</div>"
                )
            )

        return blocks

    def _render_sql(self, stmt: sa.ClauseElement) -> str:
        try:
            from sqlalchemy.dialects import postgresql
            dialect = postgresql.dialect()
        except ImportError:
            dialect = sa.create_engine("sqlite://").dialect

        compiled = stmt.compile(
            dialect=dialect,
            compile_kwargs={"literal_binds": True},
        )
        return str(compiled)

    

class MeasureRelationship(HTMLRenderable, Base):
    """Association object for n-m mapping between parent and child measures.
    
    This can't be achieved via association table alone, despite lack of additional data, due to the self-referential nature of this relationship.
    """
    __tablename__ = 'measure_relationship'
    # Not Mapped[int | None]: a primary key column cannot hold NULL, and the
    # optional annotation left Column.nullable True while every backend emitted
    # NOT NULL — a permanent 'modify_nullable' in autogenerate comparisons. The
    # attribute is still None before flush; only the column metadata changes.
    parent_measure_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('measure.measure_id'), primary_key=True)
    child_measure_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('measure.measure_id'), primary_key=True) 

    parent: so.Mapped[Measure] = so.relationship("Measure", foreign_keys=[parent_measure_id], back_populates='child_links')
    child: so.Mapped[Measure] = so.relationship("Measure", foreign_keys=[child_measure_id], back_populates='parent_links')
    
    def __repr__(self) -> str:
        return f"<MeasureRelationship parent={self.parent_measure_id} child={self.child_measure_id}>"

    def _html_css_class(self) -> str:
        return "measure"

    def _html_title(self) -> str:
        return "Measure Relationship"

    def _html_header(self) -> dict[str, object]:
        return {
            "Parent ID": self.parent_measure_id,
            "Child ID": self.child_measure_id,
            "Parent": self.parent.name if self.parent else "",
            "Child": self.child.name if self.child else "",
        }

    def _html_inner(self):
        rows = []

        if self.parent:
            rows.append(
                ["Parent", self.parent.measure_id, self.parent.name, self.parent.combination.value]
            )

        if self.child:
            rows.append(
                ["Child", self.child.measure_id, self.child.name, self.child.combination.value]
            )

        if not rows:
            return [RawHTML("<div class='muted'><i>Relationship not fully loaded</i></div>")]

        return [
            RawHTML(
                table(
                    headers=["Role", "ID", "Name", "Combination"],
                    rows=[[td(r[0]), td(r[1]), td(r[2]), td(r[3])] for r in rows],
                    cls="concept-table compact",
                )
            )
        ]


class MeasureTemporalWindow(Base):
    """Window config for a temporal-window measure (1:1 with Measure via PK)."""
    __tablename__ = "measure_temporal_window"

    measure_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey("measure.measure_id"), primary_key=True
    )
    candidate_measure_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey("measure.measure_id"), nullable=False
    )
    window_min_days: so.Mapped[int | None] = so.mapped_column(sa.Integer, nullable=True)
    window_max_days: so.Mapped[int | None] = so.mapped_column(sa.Integer, nullable=True)
    window_pick_strategy: so.Mapped[WindowPickStrategy | None] = so.mapped_column(
        sa.Enum(WindowPickStrategy), nullable=True
    )
    result_date_source: so.Mapped[ResultDateSource | None] = so.mapped_column(
        sa.Enum(ResultDateSource), nullable=True
    )
    require_same_resolver: so.Mapped[bool] = so.mapped_column(sa.Boolean, default=True)

    measure: so.Mapped[Measure] = so.relationship(
        "Measure", foreign_keys=[measure_id], back_populates="window_config"
    )
    candidate_measure: so.Mapped[Measure] = so.relationship(
        "Measure", foreign_keys=[candidate_measure_id]
    )


def _days_offset(n: int) -> sa.ColumnElement:
    """PostgreSQL INTERVAL expression for date arithmetic. Not cross-dialect portable."""
    return sa.cast(n, sa.Integer) * sa.text("INTERVAL '1 day'")


def _fmt_days_offset(n: int) -> str:
    return f"+{n}d" if n >= 0 else f"{n}d"


class MeasureSQLCompiler:
    """
    Compiles a Measure into SQLAlchemy Select constructs.

    Responsibilities
    ----------------
    - Resolve leaf subqueries
    - Recursively compile child measures
    - Apply RuleCombination semantics
    - Normalise column shape to:
        (person_id, episode_id, measure_resolver, measure_date)

    This class performs SQL compilation only.
    It does NOT execute queries.
    """
    def __init__(self, measure: Measure):
        self.measure = measure

    def _combine(self, parts: Sequence[SQLQuery]) -> SQLQuery:
        combiner = COMBINATION_SQL[self.measure.combination]
        return combiner(*parts)
    
    def _normalise(self, q: SQLQuery) -> sa.Subquery:
        """
        Ensure child query produces required canonical columns.

        All measure queries must expose:
            person_id
            episode_id
            measure_resolver
            measure_date

        Raises
        ------
        ValueError if required columns are missing.
        """
        sq = q.subquery()

        required = {"person_id", "episode_id", "measure_resolver", "measure_date"}
        missing = required - set(sq.c.keys())

        if missing:
            raise ValueError(
                f"Normalisation failed: query is missing columns {missing}. "
                f"Available columns: {list(sq.c.keys())}"
            )
        return sa.select(
            sq.c.person_id.label("person_id"),
            sq.c.episode_id.label("episode_id"),
            sq.c.measure_resolver.label("measure_resolver"),
            sq.c.measure_date.label("measure_date")
        ).subquery()

    def sql_any(self, *, ep_override: bool = False) -> SQLQuery:
        if self.measure.is_temporal_window:
            return self._sql_temporal_window_any(ep_override=ep_override)
        if self.measure.subquery is None and not self.measure.children:
            raise ValueError(f"Measure {self.measure.measure_id} has no subquery and no children")
        ep_override = ep_override or self.measure.person_ep_override

        if self.measure.subquery:
            return self.measure.subquery.get_subquery_any(ep_override=ep_override)

        children = [MeasureSQLCompiler(c) for c in self.measure.children]

        if self.measure.combination is RuleCombination.rule_or:
            #return self._combine([c.sql_any(ep_override=ep_override) for c in children])
            parts = [self._normalise(c.sql_any(ep_override=ep_override)).select() for c in children]
            return self._combine(parts)
        # AND / EXCEPT collapse to FIRST logic
        return self.sql_first(ep_override=ep_override)

    def sql_undated(self, *, ep_override: bool = False) -> SQLQuery:
        if self.measure.is_temporal_window:
            raise NotImplementedError(
                f"Measure {self.measure.measure_id!r} ({self.measure.name!r}) is a "
                "temporal_window measure and does not support undated output. "
                "Temporal_window measures should only be used as leaves of rule_or "
                "combination or as standalone numerator/denominator measures — "
                "not inside AND-combination composites."
            )
        if self.measure.subquery is None and not self.measure.children:
            raise ValueError(f"Measure {self.measure.measure_id} has no subquery and no children")
        ep_override = ep_override or self.measure.person_ep_override

        if self.measure.subquery:
            return self.measure.subquery.get_subquery_undated(ep_override=ep_override)

        children = [MeasureSQLCompiler(c) for c in self.measure.children]
        return self._combine([c.sql_undated(ep_override=ep_override) for c in children])

    def sql_first(self, *, ep_override: bool = False) -> SQLQuery:
        if self.measure.is_temporal_window:
            return self._sql_temporal_window_first(ep_override=ep_override)
        if self.measure.subquery is None and not self.measure.children:
            raise ValueError(f"Measure {self.measure.measure_id} has no subquery and no children")
        ep_override = ep_override or self.measure.person_ep_override

        if self.measure.subquery:
            return self.measure.subquery.get_subquery_first(ep_override=ep_override)
        
        children = [MeasureSQLCompiler(c) for c in self.measure.children]
        earliest = [
            self._normalise(c.sql_any(ep_override=ep_override))
            for c in children
        ]        

        # earliest = [c.sql_any(ep_override=ep_override).subquery() for c in children]
        start = earliest[0]
        lhs = start
        date_cols = [start.c.measure_date]

        date_cols = [lhs.c.measure_date]

        for rhs in earliest[1:]:
            lhs = lhs.join(
                rhs,
                start.c.measure_resolver == rhs.c.measure_resolver
            )
            date_cols.append(rhs.c.measure_date)

        return sa.select(
            start.c.person_id,
            start.c.episode_id,
            start.c.measure_resolver,
            sa.func.greatest(*date_cols).label("measure_date"),
        ).select_from(lhs)

    def _sql_temporal_window_any(self, *, ep_override: bool = False) -> SQLQuery:
        cfg = self.measure.window_config
        assert cfg is not None
        if cfg.candidate_measure is None:
            raise ValueError(
                f"Temporal window measure {self.measure.measure_id} references "
                f"candidate_measure_id={cfg.candidate_measure_id} which could not be loaded."
            )
        if self.measure.subquery is None:
            raise ValueError(
                f"Temporal window measure {self.measure.measure_id} has no subquery (anchor)."
            )

        # Step 1: anchor — deduplicated to earliest row per resolver
        anchor_sq = self._normalise(
            self.measure.subquery.get_subquery_first(ep_override=ep_override)
        )

        # Step 2: candidate — all qualifying rows
        candidate_sq = self._normalise(
            MeasureSQLCompiler(cfg.candidate_measure).sql_any(ep_override=ep_override)
        )

        # Step 3: join condition
        join_conds = [anchor_sq.c.person_id == candidate_sq.c.person_id]
        if cfg.require_same_resolver:
            join_conds.append(
                anchor_sq.c.measure_resolver == candidate_sq.c.measure_resolver
            )

        # Step 4: window predicates
        window_conds = []
        if cfg.window_min_days is not None:
            window_conds.append(
                candidate_sq.c.measure_date
                >= anchor_sq.c.measure_date + _days_offset(cfg.window_min_days)
            )
        if cfg.window_max_days is not None:
            window_conds.append(
                candidate_sq.c.measure_date
                <= anchor_sq.c.measure_date + _days_offset(cfg.window_max_days)
            )

        # Step 5: result date column
        source = cfg.result_date_source or ResultDateSource.candidate
        if source is ResultDateSource.anchor:
            result_date = anchor_sq.c.measure_date
        elif source is ResultDateSource.greatest:
            result_date = sa.func.greatest(anchor_sq.c.measure_date, candidate_sq.c.measure_date)
        elif source is ResultDateSource.least:
            result_date = sa.func.least(anchor_sq.c.measure_date, candidate_sq.c.measure_date)
        else:
            result_date = candidate_sq.c.measure_date

        # Build base carrying candidate_date and anchor_date so pick strategy can order
        # by candidate timing without being affected by the result_date transformation.
        base_q = sa.select(
            anchor_sq.c.person_id,
            anchor_sq.c.episode_id,
            anchor_sq.c.measure_resolver,
            result_date.label("measure_date"),
            candidate_sq.c.measure_date.label("_candidate_date"),
            anchor_sq.c.measure_date.label("_anchor_date"),
        ).join(candidate_sq, sa.and_(*join_conds))

        if window_conds:
            base_q = base_q.where(sa.and_(*window_conds))

        base = base_q.subquery()

        def _canonical(sq):
            return sa.select(
                sq.c.person_id,
                sq.c.episode_id,
                sq.c.measure_resolver,
                sq.c.measure_date,
            )

        # Step 6: pick strategy — ordering always on _candidate_date, not result_date
        strategy = cfg.window_pick_strategy or WindowPickStrategy.earliest
        if strategy is WindowPickStrategy.any:
            return _canonical(base)

        if strategy in (WindowPickStrategy.earliest, WindowPickStrategy.latest):
            order_expr = (
                sa.asc(base.c._candidate_date)
                if strategy is WindowPickStrategy.earliest
                else sa.desc(base.c._candidate_date)
            )
            rn_sq = sa.select(
                base,
                sa.func.row_number().over(
                    partition_by=[base.c.person_id, base.c.episode_id, base.c.measure_resolver],
                    order_by=order_expr,
                ).label("_rn"),
            ).subquery()
            return _canonical(rn_sq).where(rn_sq.c._rn == 1)

        # closest — one row per resolver, minimising |candidate_date - anchor_date|
        rn_sq = sa.select(
            base,
            sa.func.row_number().over(
                partition_by=[base.c.person_id, base.c.episode_id, base.c.measure_resolver],
                order_by=[
                    sa.func.abs(
                        sa.func.extract("epoch", base.c._candidate_date)
                        - sa.func.extract("epoch", base.c._anchor_date)
                    ),
                    base.c._candidate_date,  # prefer earlier on ties
                ],
            ).label("_rn"),
        ).subquery()
        return _canonical(rn_sq).where(rn_sq.c._rn == 1)

    def _sql_temporal_window_first(self, *, ep_override: bool = False) -> SQLQuery:
        inner = self._sql_temporal_window_any(ep_override=ep_override).subquery()
        return sa.select(
            inner.c.person_id,
            inner.c.episode_id,
            inner.c.measure_resolver,
            sa.func.min(inner.c.measure_date).label("measure_date"),
        ).group_by(inner.c.person_id, inner.c.episode_id, inner.c.measure_resolver)


class MeasureExecutor:

    """
    Executes compiled measure SQL and materialises MeasureMember objects.

    Features
    --------
    - Per-instance execution cache (by measure_id)
    - Optional person-level restriction
    - Result typing into MeasureMember dataclass

    Executor instances are intended to be short-lived
    within a report execution boundary.
    """

    def __init__(self, db):
        self.db = db
        self._cache: dict[int, Sequence[MeasureMember]] = {}

    def execute(
        self,
        measure: Measure,
        *,
        ep_override: bool = False,
        people: list[int] | None = None,
        force_refresh: bool = False,
    ) -> Sequence[MeasureMember]:
        """
        Execute a measure and return its members.

        Parameters
        ----------
        measure:
            Measure to execute.
        ep_override:
            Override episode behaviour.
        people:
            Optional person_id filter.
        force_refresh:
            Ignore execution cache.

        Returns
        -------
        Sequence[MeasureMember]
        """
        if measure.measure_id == 0:
            raise RuntimeError(
                "Measure ID = 0 represents FULL COHORT and must be resolved at the Report level. "
                "Call Report.execute() instead."
            )
        if not force_refresh and measure.measure_id in self._cache:
            rows = self._cache[measure.measure_id]
            measure._members = rows
            return rows
        
        sql = MeasureSQLCompiler(measure).sql_any(ep_override=ep_override)

        if people:
            sq = sql.subquery()
            sql = sa.select(sq).where(sq.c.person_id.in_(people))

        rows = self.db.execute(sql).all()
        rows_typed = [MeasureMember.from_row(r) for r in rows]
        self._cache[measure.measure_id] = rows_typed
        measure._members = rows_typed
        return rows_typed
    
    def members(self, measure: Measure) -> Sequence[MeasureMember]:
        try:
            return self._cache[measure.measure_id]
        except KeyError:
            raise RuntimeError(
                f"Measure {measure.measure_id} ('{measure.name}') "
                "has not been executed yet."
            )
