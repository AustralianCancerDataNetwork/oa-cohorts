from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from graphlib import CycleError, TopologicalSorter

import sqlalchemy as sa
import sqlalchemy.orm as so
from orm_loader.helpers import Base
from sqlalchemy.exc import DBAPIError

from ..core import ResultDateSource, RuleCombination, WindowPickStrategy
from ..core.executability import ExecStatus, MeasureExecCheck
from ..core.html_utils import (
    HTMLChild,
    HTMLRenderable,
    RawHTML,
    esc,
    render_sql,
    sql_block,
    table,
    td,
)
from ..errors import (  # noqa: F401 -- compatible error re-export
    MissingMaterializedViewError,
    reraise_schema_error,
)
from .evidence import EvidenceCompiler, canonical_query
from .subquery import Subquery
from .typing import SQLQuery, _first_member_query


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

    source_table: str | None = None
    source_event_id: int | None = None
    opportunity_id: int | None = None
    source_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    completion_date: date | None = None
    anchor_date: date | None = None
    candidate_date: date | None = None
    anchor_event_id: int | None = None
    candidate_event_id: int | None = None
    date_required: bool = True

    @classmethod
    def from_row(cls, r):
        return cls(
            person_id=r.person_id,
            measure_resolver=r.measure_resolver,
            episode_id=getattr(r, "episode_id", None),
            measure_date=getattr(r, "measure_date", None),
            source_table=getattr(r, "source_table", None),
            source_event_id=getattr(r, "source_event_id", None),
            opportunity_id=getattr(r, "opportunity_id", None),
            source_date=getattr(r, "source_date", None),
            start_date=getattr(r, "start_date", None),
            end_date=getattr(r, "end_date", None),
            completion_date=getattr(r, "completion_date", None),
            anchor_date=getattr(r, "anchor_date", None),
            candidate_date=getattr(r, "candidate_date", None),
            anchor_event_id=getattr(r, "anchor_event_id", None),
            candidate_event_id=getattr(r, "candidate_event_id", None),
            date_required=getattr(r, "date_required", True),
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
    __table_args__ = (sa.CheckConstraint("matching_scope IN ('cancer', 'treatment')", name="measure_matching_scope"),)
    measure_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    id = so.synonym("measure_id")

    name: so.Mapped[str] = so.mapped_column(sa.String(250))
    combination: so.Mapped[RuleCombination] = so.mapped_column(sa.Enum(RuleCombination))

    subquery_id: so.Mapped[int | None] = so.mapped_column(sa.ForeignKey("subquery.subquery_id"), nullable=True)
    matching_scope: so.Mapped[str] = so.mapped_column(sa.String(9), default="cancer", server_default="cancer", nullable=False)
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
        ]
        not_applicable: tuple[str, ...] = ()
        if not self.is_temporal_window:
            checks.append(("UNDATED", compiler.sql_undated))
        else:
            not_applicable = ("UNDATED",)

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
            not_applicable_variants=not_applicable,
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
            anchor = cfg.anchor_measure or self.subquery
            candidate_name = (
                cfg.candidate_measure.name if cfg.candidate_measure else str(cfg.candidate_measure_id)
            )
            return {
                "ID": self.measure_id,
                "Name": self.name,
                "Kind": RawHTML("<span class='badge temporal_window'>TEMPORAL WINDOW</span>"),
                "Anchor": anchor.name if anchor else RawHTML("<i>missing</i>"),
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

        cfg = self.window_config
        if cfg is not None:
            anchor = cfg.anchor_measure or self.subquery
            if anchor is not None:
                blocks.append(RawHTML("<div class='subquery-section-title'>Anchor</div>"))
                blocks.append(anchor)
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
        return render_sql(stmt)

    

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
    __table_args__ = (
        sa.CheckConstraint("anchor_selection IN ('first', 'latest', 'all')", name="window_anchor_selection"),
        sa.CheckConstraint("anchor_order_date_role IN ('measure', 'start', 'end', 'completion')", name="window_anchor_order_date_role"),
        sa.CheckConstraint("anchor_date_role IN ('measure', 'start', 'end', 'completion')", name="window_anchor_date_role"),
        sa.CheckConstraint("candidate_date_role IN ('measure', 'start', 'end', 'completion')", name="window_candidate_date_role"),
    )
    anchor_selection: so.Mapped[str] = so.mapped_column(sa.String(6), default="first", server_default="first", nullable=False)
    anchor_order_date_role: so.Mapped[str] = so.mapped_column(sa.String(10), default="measure", server_default="measure", nullable=False)
    anchor_date_role: so.Mapped[str] = so.mapped_column(sa.String(10), default="measure", server_default="measure", nullable=False)
    candidate_date_role: so.Mapped[str] = so.mapped_column(sa.String(10), default="measure", server_default="measure", nullable=False)

    measure_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey("measure.measure_id"), primary_key=True
    )
    anchor_measure_id: so.Mapped[int | None] = so.mapped_column(
        sa.ForeignKey("measure.measure_id"), nullable=True
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
    anchor_measure: so.Mapped[Measure | None] = so.relationship(
        "Measure", foreign_keys=[anchor_measure_id]
    )
    candidate_measure: so.Mapped[Measure] = so.relationship(
        "Measure", foreign_keys=[candidate_measure_id]
    )


def measure_dependencies(measure: Measure) -> list[Measure]:
    """Return direct child and temporal-window dependencies without validation."""
    dependencies = list(measure.children)
    if measure.is_temporal_window:
        config = measure.window_config
        if config is not None:
            for dependency in (
                getattr(config, "candidate_measure", None),
                getattr(config, "anchor_measure", None),
            ):
                if dependency is not None:
                    dependencies.append(dependency)
    return dependencies


def _window_dependencies(measure: Measure) -> list[Measure]:
    cfg = measure.window_config
    if cfg is None:
        raise ValueError(f"Measure {measure.measure_id} has no temporal-window configuration")
    choices = {"anchor_selection": ("first", "latest", "all"),
               "anchor_order_date_role": ("measure", "start", "end", "completion"),
               "anchor_date_role": ("measure", "start", "end", "completion"),
               "candidate_date_role": ("measure", "start", "end", "completion")}
    for field, allowed in choices.items():
        value = getattr(cfg, field, None)
        if value is not None and value not in allowed:
            raise ValueError(f"Invalid {field}: {value!r}")
    anchor = getattr(cfg, "anchor_measure", None)
    if getattr(cfg, "anchor_measure_id", None) is not None and anchor is None:
        raise ValueError(f"Measure {measure.measure_id} has a missing anchor measure")
    if (measure.subquery is not None) == (anchor is not None):
        raise ValueError(
            f"Measure {measure.measure_id} requires exactly one anchor: subquery or measure"
        )
    if cfg.candidate_measure is None:
        raise ValueError(f"Measure {measure.measure_id} has a missing candidate measure")
    if (
        cfg.window_min_days is not None
        and cfg.window_max_days is not None
        and cfg.window_min_days > cfg.window_max_days
    ):
        raise ValueError(f"Measure {measure.measure_id} has reversed window bounds")
    return [cfg.candidate_measure] if anchor is None else [cfg.candidate_measure, anchor]


def _validate_measure_dependencies(measure: Measure) -> None:
    """Check anchor contracts and cycles without compiling SQL or executing CDM queries."""
    nodes = {}
    graph = {}
    pending = [measure]
    while pending:
        node = pending.pop()
        identity = id(node)
        if identity in nodes:
            continue
        nodes[identity] = node
        if getattr(node, "matching_scope", None) not in (None, "cancer", "treatment"):
            raise ValueError(f"Invalid matching_scope for measure {node.measure_id}")
        dependencies = list(node.children)
        if node.is_temporal_window:
            dependencies.extend(_window_dependencies(node))
        graph[identity] = [id(dependency) for dependency in dependencies]
        pending.extend(dependencies)
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as error:
        cycle = [nodes[identity].measure_id for identity in error.args[1]]
        raise ValueError(f"Measure dependency cycle: {cycle}") from error


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

    def sql_evidence(self, *, ep_override: bool = False) -> SQLQuery:
        """Preserve physical identity, opportunity and named date roles."""
        _validate_measure_dependencies(self.measure)
        return EvidenceCompiler(self.measure).query(ep_override=ep_override)

    def sql_any(self, *, ep_override: bool = False) -> SQLQuery:
        return canonical_query(self.sql_evidence(ep_override=ep_override))

    def sql_undated(self, *, ep_override: bool = False) -> SQLQuery:
        _validate_measure_dependencies(self.measure)
        if self.measure.is_temporal_window:
            raise NotImplementedError(
                f"Measure {self.measure.measure_id!r} ({self.measure.name!r}) is a "
                "temporal_window measure and does not support undated output. "
                "Use the dated ANY or FIRST variants, including in dated composites."
            )
        source = self.sql_evidence(ep_override=ep_override).subquery()
        return sa.select(source.c.person_id, source.c.episode_id, source.c.measure_resolver).distinct()

    def sql_first(self, *, ep_override: bool = False) -> SQLQuery:
        """Earliest qualifying emitted date; never discard evidence in children."""
        return _first_member_query(self.sql_any(ep_override=ep_override))

    def sql_qualification(self, *, ep_override: bool = False) -> SQLQuery:
        """Earliest completion, separate from a window's emitted date."""
        evidence = self.sql_evidence(ep_override=ep_override).subquery()
        return _first_member_query(sa.select(
            evidence.c.person_id, evidence.c.episode_id, evidence.c.measure_resolver,
            evidence.c.completion_date.label("measure_date")))


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
        self._bindings: dict[int, tuple[bool, tuple[int, ...] | None]] = {}

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
        binding = (ep_override, tuple(sorted(set(people))) if people is not None else None)
        if (not force_refresh and measure.measure_id in self._cache
                and self._bindings.get(measure.measure_id, (False, None)) == binding):
            rows = self._cache[measure.measure_id]
            measure._members = rows
            return rows
        
        sql = MeasureSQLCompiler(measure).sql_evidence(ep_override=ep_override)

        if people is not None:
            sq = sql.subquery()
            sql = sa.select(sq).where(sq.c.person_id.in_(people))

        try:
            rows = self.db.execute(sql).all()
        except DBAPIError as exc:
            reraise_schema_error(exc, context=f"Measure '{measure.name}' (ID {measure.measure_id})")
            raise
        rows_typed = [MeasureMember.from_row(r) for r in rows]
        self._bindings[measure.measure_id] = binding
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
