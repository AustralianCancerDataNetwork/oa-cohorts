from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
import sqlalchemy.orm as so
from orm_loader.helpers import Base
from sqlalchemy.ext.associationproxy import association_proxy

from ..core.executability import ExecStatus, MeasureExecCheck
from ..core.html_utils import HTMLRenderable, RawHTML

if TYPE_CHECKING:
    from .measure import Measure, MeasureExecutor, MeasureMember
    from .report import ReportCohortMap

from sqlalchemy.engine import Row as SARow

Row = SARow[Any]

"""Association table for n-m mapping between dash_cohort and dash_cohort_def"""
dash_cohort_def_map = sa.Table(
    'dash_cohort_def_map', 
    Base.metadata,
    sa.Column('dash_cohort_def_id', sa.ForeignKey('dash_cohort_def.dash_cohort_def_id')),
    sa.Column('dash_cohort_id', sa.ForeignKey('dash_cohort.dash_cohort_id'))
)

class DashCohortDef(HTMLRenderable, Base):
    """
    Conceptually-useful filtering units for end users.

    A DashCohortDef is a thin wrapper around a single Measure.
    It exists to:

    - Provide naming and grouping semantics
    - Allow reuse of measures across multiple cohorts
    - Expose a user-friendly abstraction

    Execution is delegated to the backing measure.
    """
    __tablename__ = 'dash_cohort_def'

    dash_cohort_def_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    id = so.synonym('dash_cohort_def_id')

    dash_cohort_def_name: so.Mapped[str] = so.mapped_column(sa.String(250))
    dash_cohort_def_short_name: so.Mapped[str] = so.mapped_column(sa.String(50))
    measure_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('measure.measure_id'))

    dash_cohort_objects: so.Mapped[list[DashCohort]] = so.relationship(
        secondary=dash_cohort_def_map,
        back_populates="definitions",
    )

    dash_cohort_measure: so.Mapped[Measure] = so.relationship(
        "Measure",
        foreign_keys=[measure_id],
        lazy="joined",
    )

    def members(self, executor: MeasureExecutor) -> Sequence[MeasureMember]:
        """
        Members of a cohort definition are exactly the members of its backing measure.
        Assumes the measure has already been executed.
        """
        if not self.dash_cohort_measure:
            return ()
        return self.dash_cohort_measure.members(executor)

    def get_cohort(self):
        return self.dash_cohort_measure

    @property
    def measure_count(self):
        if self.dash_cohort_measure:
            return getattr(self.dash_cohort_measure, "measure_count", 1)
        return 0

    def __repr__(self):
        return (
            f"<DashCohortDef {self.dash_cohort_def_name!r} "
            f"measure={self.measure_id}>"
        )

    def _html_css_class(self) -> str:
        return "dash-cohort-def"

    def _html_title(self) -> str:
        return f"Cohort Definition: {self.dash_cohort_def_name}"

    def _html_header(self) -> dict[str, str]:
        hdr = {
            "ID": str(self.dash_cohort_def_id),
            "Short name": self.dash_cohort_def_short_name,
        }

        if self.dash_cohort_measure:
            hdr["Measure"] = self.dash_cohort_measure.name
            hdr["Measure ID"] = str(self.measure_id)

        return hdr

    def _html_inner(self):
        blocks: list[object] = []

        if self.dash_cohort_measure:
            blocks.append(self.dash_cohort_measure)
        else:
            blocks.append(RawHTML("<div class='muted'><i>No measure linked</i></div>"))

        return blocks
    

    def is_executable(self) -> MeasureExecCheck:
        """
        A cohort definition is executable iff its backing measure is executable.
        """
        if not self.dash_cohort_measure:
            return MeasureExecCheck(
                status=ExecStatus.FAIL,
                ok_variants=[],
                failed_variants={"MEASURE": "No measure linked to cohort definition"},
            )

        return self.dash_cohort_measure.is_executable()

class DashCohort(HTMLRenderable, Base):
    """
    Logical population composed of one or more DashCohortDef objects.

    Members of a DashCohort are the union of members from its
    component definitions.

    Cohorts do not generate SQL directly.
    They orchestrate execution of their underlying measures.
    """
    __tablename__ = 'dash_cohort'

    dash_cohort_id: so.Mapped[int] = so.mapped_column(sa.Integer, primary_key=True)
    id = so.synonym('dash_cohort_id')

    dash_cohort_name: so.Mapped[str] = so.mapped_column(sa.String(250))

    in_reports: so.Mapped[list[ReportCohortMap]] = so.relationship(back_populates='cohort')
    definitions: so.Mapped[list[DashCohortDef]] = so.relationship(
        secondary=dash_cohort_def_map,
        back_populates="dash_cohort_objects",
        lazy="selectin",
    )

    measures = association_proxy("definitions", "dash_cohort_measure")
    measure_ids = association_proxy("definitions", "measure_id")

    @property
    def cohort_def_labels(self):
        return [
            (self.dash_cohort_name, d.dash_cohort_def_name, d.measure_id)
            for d in self.definitions
        ]

    def members(self, executor: MeasureExecutor) -> Sequence[MeasureMember]:
        seen = set()
        out: list[MeasureMember] = []

        for d in self.definitions:
            for row in d.members(executor):
                if row not in seen:
                    seen.add(row)
                    out.append(row)

        return out
    
    @property
    def definition_count(self):
        return len(self.definitions)

    @property
    def measure_count(self):
        return len(self.measures)
    
    def execute(self, db: so.Session, *, people: list[int] | None = None):
        from .measure import MeasureExecutor

        executor = MeasureExecutor(db)
        for d in self.definitions:
            if d.dash_cohort_measure:
                executor.execute(
                    d.dash_cohort_measure,
                    people=people,
                )

    def __repr__(self):
        return f"<DashCohort {self.dash_cohort_name!r} defs={len(self.definitions)}>"

    def _html_css_class(self) -> str:
        return "dash-cohort"

    def _html_title(self) -> str:
        return f"Cohort: {self.dash_cohort_name}"

    def _html_header(self) -> dict[str, str]:
        return {
            "ID": str(self.dash_cohort_id),
            "Definitions": str(self.definition_count),
            "Measures": str(self.measure_count),
        }

    def _html_inner(self):
        blocks: list[object] = []

        blocks.append(RawHTML("<div class='subquery-section-title'>Definitions</div>"))

        if self.definitions:
            blocks.extend(
                sorted(
                    self.definitions,
                    key=lambda d: d.dash_cohort_def_name,  # type: ignore
                )
            )
        else:
            blocks.append(RawHTML("<div class='muted'><i>No cohort definitions</i></div>"))

        return blocks
    
