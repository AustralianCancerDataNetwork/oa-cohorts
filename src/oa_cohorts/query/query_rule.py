from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar

import sqlalchemy as sa
import sqlalchemy.orm as so
from omop_alchemy.cdm.model import Concept
from orm_loader.helpers import Base

from ..core import RuleMatcher, RuleTarget, ThresholdDirection
from ..core.html_utils import (
    HTMLChild,
    HTMLRenderable,
    RawHTML,
    esc,
    render_sql,
    table,
    td,
)
from ..measurables import get_measurable_registry
from .phenotype import Phenotype


class QueryRule(Base, HTMLRenderable):
    """
    Base class for all atomic filtering rules used in subqueries.

    A QueryRule represents a single, clinically-meaningful condition that can be
    applied to a specific OMOP field (e.g. diagnosis concept, procedure concept,
    measurement value, demographic attribute). Rules are combined at the Subquery
    and Measure layers to construct cohort definitions and quality indicators.

    This class is polymorphic over `RuleMatcher`, with concrete subclasses
    implementing different matching semantics such as exact match, hierarchy
    expansion, substring matching, scalar thresholds, phenotype expansion, and
    presence/absence.

    QueryRules are intentionally low-level and declarative: they do not encode
    clinical intent directly, but provide the building blocks from which clinically
    grounded cohort and indicator logic can be composed.
    """
    __tablename__ = "query_rule"

    query_rule_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    matcher: so.Mapped[RuleMatcher] = so.mapped_column(sa.Enum(RuleMatcher))
    # Concept references are intentionally not schema-enforced here. Config import
    # and authoring need to tolerate unresolved concept ids so they can surface
    # missing-vocabulary problems at execution time instead of failing on write.
    concept_id: so.Mapped[int | None] = so.mapped_column(
        sa.Integer, nullable=True, index=True
    )
    notes: so.Mapped[str | None] = so.mapped_column(sa.String, nullable=True)

    # scalar support
    scalar_threshold: so.Mapped[int | None] = so.mapped_column(sa.Integer, nullable=True)
    threshold_direction: so.Mapped[ThresholdDirection | None] = so.mapped_column(sa.Enum(ThresholdDirection), nullable=True)    
    threshold_comparator: so.Mapped[RuleTarget | None] = so.mapped_column(sa.Enum(RuleTarget), nullable=True)

    # phenotype support
    phenotype_id: so.Mapped[int | None] = so.mapped_column(
        sa.ForeignKey("phenotype.phenotype_id"), nullable=True, index=True
    )
    # SQLAlchemy declares `__mapper_args__: Any` (instance-level) on
    # DeclarativeBase, so ty reads ClassVar here as an LSP violation. RUF012
    # accepts nothing but ClassVar, so suppress on this class 
    __mapper_args__: ClassVar[dict[str, Any]] = {  # ty: ignore[invalid-attribute-override]
        "polymorphic_on": matcher,
    }

    # relationships
    concept: so.Mapped[Concept | None] = so.relationship(
        Concept,
        primaryjoin=lambda: so.foreign(QueryRule.concept_id) == Concept.concept_id,
        lazy="select",
    )
    phenotype: so.Mapped[Phenotype | None] = so.relationship(Phenotype,lazy="joined")

    @property
    def requires_numeric(self) -> bool:
        return self.matcher == RuleMatcher.scalar

    @property
    def requires_string(self) -> bool:
        return self.matcher == RuleMatcher.substring
    
    @property
    def requires_predicate(self) -> bool:
        return self.matcher == RuleMatcher.predicate

    def get_filter_details(self, field: sa.ColumnElement[Any]) -> sa.ColumnElement[bool]:
        """
        Return the SQLAlchemy boolean expression implementing this rule against
        a specific target field.

        This method must be implemented by subclasses and is responsible for
        translating the rule semantics (e.g. exact match, hierarchy expansion,
        threshold comparison) into a SQL WHERE clause fragment.

        Parameters
        ----------
        field:
            The SQLAlchemy column or expression that this rule should be applied to.

        Returns
        -------
        A SQLAlchemy boolean expression suitable for inclusion in a WHERE clause.
        """
        raise NotImplementedError("get_filter_details must be implemented on subclasses")
    
    def sql_preview(self, field: sa.ColumnElement[Any]) -> str:
        """
        Return a SQL WHERE fragment representing this rule.
        """
        expr = self.get_filter_details(field)
        return render_sql(expr)


    def __lt__(self, other: object) -> bool:
        if not isinstance(other, QueryRule):
            return NotImplemented

        if self.query_rule_id != other.query_rule_id:
            return self.query_rule_id < other.query_rule_id

        return (
             self.concept_id is None,
             self.concept_id if self.concept_id is not None else -1,
        ) < (
             other.concept_id is None,
             other.concept_id if other.concept_id is not None else -1,
        )

    def __repr__(self) -> str:
        parts = [f"id={self.query_rule_id}", self.matcher.value]

        if self.concept_id:
            parts.append(f"concept={self.concept_id}")

        if self.scalar_threshold is not None:
            if self.threshold_direction is None:
                raise ValueError(f"Rule {self.query_rule_id} has scalar_threshold but no threshold_direction")
            parts.append(f"{self.threshold_direction.value}{self.scalar_threshold}")
            if self.threshold_comparator:
                parts.append(f"on={self.threshold_comparator.value}")

        if self.phenotype_id is not None:
            parts.append(f"phenotype={self.phenotype.phenotype_name if self.phenotype else self.phenotype_id}")

        if self.notes:
            parts.append(f"note={self.notes!r}")

        return f"<{self.__class__.__name__} {' '.join(parts)}>"


    def _html_css_class(self) -> str:
        return "queryrule"
    
    def _html_title(self) -> str:
        return f"Rule #{self.query_rule_id} — {self.matcher.value}"

    def _html_header(self) -> dict[str, str]:
        hdr: dict[str, str] = {
            "Type": self.matcher.value,
        }

        if self.concept and self.concept_id:
            hdr["Concept"] = f"{self.concept.concept_name} ({self.concept_id})"

        if self.phenotype:
            hdr["Phenotype"] = self.phenotype.phenotype_name

        if self.scalar_threshold is not None and self.threshold_direction is not None:
            hdr["Threshold"] = f"{self.threshold_direction.value}{self.scalar_threshold}"

        if self.threshold_comparator:
            hdr["On"] = self.threshold_comparator.value

        if self.notes:
            hdr["Notes"] = self.notes

        return hdr

    def _html_inner(self) -> Iterable[HTMLChild]:
        blocks: list[HTMLChild] = []

        # Optional SQL preview (WHERE clause only)
        try:
            blocks.append(RawHTML("<div class='subquery-section-title'>SQL filter</div>"))

            # This is a WHERE fragment, not a full query
            # Caller (Subquery) chooses the field
            blocks.append(
                RawHTML(
                    "<div class='muted'>This rule contributes a WHERE clause fragment.</div>"
                )
            )
        except Exception as e:
            blocks.append(
                RawHTML(f"<div class='sql-error'>SQL preview failed: {esc(e)}</div>")
            )

        return blocks


class ExactRule(QueryRule):
    """
    Rule implementing exact concept matching.

    This rule filters records where the target field matches a single OMOP
    concept_id exactly. It is typically used for precise clinical concepts
    (e.g. a specific diagnosis code, procedure concept, or measurement concept).

    Clinically, this corresponds to strict inclusion of a well-defined coded event.
    """

    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_identity": RuleMatcher.exact,
    }

    @property
    def comparator(self):
        # for exact match we are comparing against the actual concept_id
        if not self.concept:
            raise RuntimeError(f'Rule concept {self.concept_id} not found')
        return self.concept_id

    def get_filter_details(self, field: sa.ColumnElement[Any]) -> sa.ColumnElement[bool]:
        return field.__eq__(self.comparator)

class HierarchyBase(QueryRule):
    """
    Abstract base class for hierarchy-based rules.

    Hierarchy rules expand a single parent OMOP concept into all of its descendant
    concepts using the OMOP concept_ancestor table. This allows cohort definitions
    to operate at clinically meaningful category levels (e.g. "lung cancer" rather
    than enumerating every histological subtype).

    Subclasses define whether the expanded hierarchy is included or excluded.
    """

    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_abstract": True,
    }

    children: so.Mapped[list[Concept]] = so.relationship(
        Concept,
        secondary="concept_ancestor",
        primaryjoin="QueryRule.concept_id == concept_ancestor.c.ancestor_concept_id",
        secondaryjoin="concept_ancestor.c.descendant_concept_id == Concept.concept_id",
        lazy="selectin",
    )

    @property
    def comparator(self):
        # for hierarchical match we are comparing against the actual concept_id, plus the set of child concept_ids
        if not self.concept:
            raise RuntimeError(f'Rule concept {self.concept_id} not found')
        return [c.concept_id for c in self.children]
    
    def _html_inner(self) -> Iterable[HTMLChild]:
        if not self.children:
            return []

        children = sorted(self.children, key=lambda c: c.concept_id)
        n = len(children)

        preview = children[:5]
        preview_rows = [
            [td(c.concept_id), td(c.concept_name), td(c.vocabulary_id)]
            for c in preview
        ]

        summary = RawHTML(
            table(
                headers=["Concept ID", "Name", "Vocabulary"],
                rows=preview_rows,
                cls="concept-table compact",
            )
        )

        tail = ""
        if n > 5:
            tail = RawHTML(f"<div class='muted'>… and {n - 5} more descendants</div>")

        return [summary, tail]

class HierarchyRule(HierarchyBase):
    """
    Rule implementing inclusive hierarchical concept matching.

    This rule matches records whose concept is either the specified parent
    concept or any of its descendants in the OMOP concept hierarchy.

    Clinically, this supports defining cohorts using broad disease groupings
    or procedure families without hard-coding long lists of concept IDs.
    """
    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_identity": RuleMatcher.hierarchy,
    }

    def get_filter_details(self, field: sa.ColumnElement[Any]) -> sa.ColumnElement[bool]:
        return field.in_(self.comparator)

class HierarchyExclusionRule(HierarchyBase):
    """
    Rule implementing exclusion over a concept hierarchy.

    This rule excludes records whose concept is within a specified hierarchy.
    It is typically used to express clinical exclusions such as
    "all cancers except small cell lung cancer" or
    "all procedures except palliative procedures".
    """
    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_identity": RuleMatcher.hierarchyexclusion,
    }

    def get_filter_details(self, field: sa.ColumnElement[Any]) -> sa.ColumnElement[bool]:
        return field.not_in(self.comparator)

class AbsenceRule(QueryRule):
    """
    Rule expressing the absence of a value or concept.

    This rule matches records where the target field is NULL, and is used to
    express clinical concepts such as the absence of a recorded diagnosis,
    missing measurements, or lack of documented events.

    Clinically, this supports negative definitions (e.g. "no documented metastases").
    """
    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_identity": RuleMatcher.absence,
    }

    @property
    def comparator(self):
        # this can validly be null or 0
        return self.concept_id

    def get_filter_details(self, field: sa.ColumnElement[Any]) -> sa.ColumnElement[bool]:
        return field.is_(None)

class PresenceRule(QueryRule):
    """
    Rule expressing the presence of any value.

    This rule matches records where the target field is non-NULL, without
    constraining the specific concept or value. It is used to test whether
    a given clinical domain has any recorded data for a patient or episode.

    Clinically, this supports definitions like "has any recorded procedure"
    or "has any recorded observation of this type".
    """
    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_identity": RuleMatcher.presence,
    }

    @property
    def comparator(self) -> int | None:
        # this can validly be null or 0
        return self.concept_id

    def get_filter_details(self, field: sa.ColumnElement[Any]) -> sa.ColumnElement[bool]:
        return field.is_not(None)

class ScalarRule(QueryRule):
    """
    Rule implementing numeric threshold comparisons on measurements.

    Scalar rules apply threshold logic (>, <, =, !=) to numeric-valued fields,
    typically measurements or derived numeric modifiers. The rule can optionally
    constrain the comparison to a specific concept (e.g. a particular lab test).
    A ``concept_id`` of ``0`` is treated as "no concept restriction", allowing
    threshold-only comparisons against measurables that expose a numeric value
    column but no concept-matching column.

    Clinically, this supports definitions such as:
    - "ECOG performance status ≥ 2"
    - "Haemoglobin < 100 g/L"
    - "Time to treatment > 30 days"
    """

    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_identity": RuleMatcher.scalar,
    }

    @property
    def comparator(self) -> tuple[int, int]:
        """Return the numeric threshold plus the optional concept discriminator."""
        if self.scalar_threshold is None:
            raise RuntimeError(f'Scalar threshold is not set on rule {self.query_rule_id}')
        if not self.concept and not self.concept_id == 0:
            raise RuntimeError(f'Rule concept {self.concept_id} not found')
        concept_id = self.concept_id if self.concept_id is not None else 0
        return self.scalar_threshold, concept_id

    @property
    def scalar_field(self):
        """
        Resolve the numeric value column associated with the target measurable.

        This indirection allows scalar rules to remain declarative while supporting
        different numeric value fields depending on the clinical domain (e.g.
        measurement.value_as_number, derived episode-level modifiers, etc.).

        Returns
        -------
        A SQLAlchemy column representing the numeric value to compare against.
        """
        if self.threshold_comparator is None:
            raise RuntimeError(f'Threshold comparator is not set on rule {self.query_rule_id}')
        measurable = get_measurable_registry()[self.threshold_comparator]
        col = measurable.__bound_measurable__.value_numeric_col
        if col is None:
            raise RuntimeError(
                f"{measurable.__name__} does not expose a numeric value column"
            )
        return col

    def get_filter_details(self, field: sa.ColumnElement[Any]) -> sa.ColumnElement[bool]:
        threshold, concept = self.comparator
        if self.threshold_direction is None:
            raise RuntimeError(f'Threshold direction is not set on scalar rule {self.query_rule_id}')

        numeric_field = self.scalar_field

        concept_clause = sa.true() if concept == 0 else field == concept

        if self.threshold_direction == ThresholdDirection.gt:
            threshold_clause = numeric_field > threshold
        elif self.threshold_direction == ThresholdDirection.lt:
            threshold_clause = numeric_field < threshold
        elif self.threshold_direction == ThresholdDirection.eq:
            threshold_clause = numeric_field == threshold
        elif self.threshold_direction == ThresholdDirection.neq:
            threshold_clause = numeric_field != threshold
        else:
            raise ValueError(f'Unknown threshold direction: {self.threshold_direction}')
        
        return sa.and_(concept_clause, threshold_clause)    
    
    def _html_inner(self) -> Iterable[HTMLChild]:
        bits: list[HTMLChild] = []
        bits.append(RawHTML(f"<div><b>Comparator:</b> {self.threshold_direction.value} {self.scalar_threshold}</div>")) # type: ignore

        if self.threshold_comparator:
            bits.append(RawHTML(f"<div><b>Target:</b> {self.threshold_comparator.value}</div>"))

        return bits
    
class PredicateRule(QueryRule):
    """
    Rule implementing boolean predicate matching.

    Predicate rules operate on measurable classes that expose a boolean
    predicate column (e.g. rt, sact, metastatic_flag). This allows filtering
    directly on derived clinical indicators without requiring concept or
    scalar comparisons.

    Clinically this supports constructs such as:
        - "episode includes radiotherapy"
        - "treatment is systemic therapy"
        - "disease is metastatic"
    """

    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_identity": RuleMatcher.predicate,
    }

    @property
    def comparator(self) -> bool:
        """
        Whether the predicate must evaluate to TRUE or FALSE.

        Default behaviour:
            concept_id is None: predicate must be TRUE
            concept_id = 1: predicate must be TRUE
            concept_id = 0: predicate must be FALSE

        Any non-zero concept id is coerced to ``True`` because predicate rules
        use ``concept_id`` as a lightweight boolean flag rather than as a real
        OMOP concept reference.
        """
        if self.concept_id is None:
            return True
        return bool(self.concept_id)
    

    def get_filter_details(self, field: sa.ColumnElement[Any]) -> sa.ColumnElement[bool]:
        """
        Apply predicate match.

        field is expected to be a boolean SQL column.
        """
        return field.is_(self.comparator)
    

    def _html_inner(self) -> Iterable[HTMLChild]:
        state = "TRUE" if self.comparator else "FALSE"
        return [
            RawHTML(f"<div><b>Predicate:</b> {state}</div>")
        ]

class PhenotypeRule(QueryRule):
    """
    Rule implementing phenotype-based matching.

    Phenotype rules expand a phenotype definition into a potentially large set
    of OMOP concept IDs and apply inclusion logic over that set. This provides
    a scalable alternative to large collections of ExactRules when a phenotype
    definition is clinically meaningful but not hierarchically structured.

    Clinically, this supports constructs such as:
    - composite disease definitions,
    - curated clinical phenotypes,
    - research-defined concept groupings.
    """
    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_identity": RuleMatcher.phenotype,
    }

    @property
    def comparator(self):
        if self.phenotype_id is None:
            raise RuntimeError(f'Phenotype is not set on rule {self.query_rule_id}')
        if not self.phenotype:
            raise RuntimeError(f'Rule phenotype {self.phenotype_id} not found')
        return [c.concept_id for c in self.phenotype.phenotype_concepts]

    def get_filter_details(self, field: sa.ColumnElement[Any]) -> sa.ColumnElement[bool]:
        return field.in_(self.comparator)
    

    def _html_inner(self) -> Iterable[HTMLChild]:
        if not self.phenotype:
            return []

        concepts = sorted(
            self.phenotype.phenotype_concepts,
            key=lambda c: (c.vocabulary_id or "", c.concept_id),
        )
        n = len(concepts)

        preview = concepts[:5]
        rows = [
            [td(c.concept_id), td(c.concept_name), td(c.vocabulary_id)]
            for c in preview
        ]

        summary = RawHTML(
            table(
                headers=["Concept ID", "Name", "Vocabulary"],
                rows=rows,
                cls="concept-table compact",
            )
        )

        tail = ""
        if n > 5:
            tail = RawHTML(f"<div class='muted'>… and {n - 5} more phenotype concepts</div>")

        return [summary, tail]
        

class SubstringRule(QueryRule):
    """
    Rule implementing substring matching on concept codes.

    This rule matches records where the target concept code contains a given
    substring. It is primarily used for legacy coding systems or clinical
    groupings that are encoded using structured code prefixes (e.g. ICD blocks).

    Clinically, this supports coarse-grained grouping when hierarchical
    relationships are unavailable or insufficiently curated.
    """
    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_identity": RuleMatcher.substring,
    }
    @property
    def comparator(self):
        # for substring match we are comparing against the concept code
        if not self.concept:
            raise RuntimeError(f'Rule concept {self.concept_id} not found')
        return self.concept.concept_code

    def get_filter_details(self, field: sa.ColumnElement[Any]) -> sa.ColumnElement[bool]:
        return field.ilike(f'%{self.comparator}%')
    

    def _html_inner(self) -> Iterable[HTMLChild]:
        if not self.concept:
            return []

        return [
            RawHTML(f"<div><b>Substring:</b> {self.concept.concept_code}</div>")
        ]
