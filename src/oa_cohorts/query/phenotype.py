from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.orm as so
from omop_alchemy.cdm.model import Concept
from orm_loader.helpers import Base
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy

from ..core.html_utils import HTMLRenderable, RawHTML, table, td


class Phenotype(HTMLRenderable, Base):
    __tablename__ = "phenotype"

    phenotype_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    phenotype_name: so.Mapped[str] = so.mapped_column(sa.String, unique=True)
    description: so.Mapped[str | None] = so.mapped_column(sa.String, nullable=True)

    # relationships
    phenotype_definitions: so.Mapped[list[PhenotypeDefinition]] = so.relationship(
        "PhenotypeDefinition",
        back_populates='phenotype',
        lazy="selectin",  
    )
    phenotype_concepts: AssociationProxy[list[Concept]] = association_proxy(
        "phenotype_definitions", "concept"
    )

    def __repr__(self) -> str:
        return f"<Phenotype {self.phenotype_name} ({len(self.phenotype_concepts)} concepts)>"

    def _html_title(self) -> str:
        return f"Phenotype: {self.phenotype_name}"

    def _html_header(self) -> dict[str, str]:
        return {
            "ID": str(self.phenotype_id),
            "Name": self.phenotype_name,
            "Description": self.description or "",
            "Concept count": str(len(self.phenotype_concepts)),
        }

    def _html_inner(self):
        concepts = sorted(
            self.phenotype_concepts,
            key=lambda c: (c.vocabulary_id or "", c.concept_id),
        )
        rows = [
            [td(c.concept_id), td(c.concept_name), td(c.vocabulary_id)]
            for c in concepts
        ]

        return [
            RawHTML(
                table(
                    headers=["Concept ID", "Name", "Vocabulary"],
                    rows=rows,
                    cls="concept-table",
                )
            )
        ]

class PhenotypeDefinition(HTMLRenderable, Base):
    __tablename__ = 'phenotype_definition'
    __table_args__ = (
        sa.UniqueConstraint("phenotype_id", "query_concept_id"),
    )

    phenotype_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('phenotype.phenotype_id'), primary_key=True)
    id = so.synonym('phenotype_id')
    # Query concept ids are intentionally stored without a schema-level foreign
    # key so config import can preserve unresolved concept references until the
    # relevant vocabulary is loaded.
    query_concept_id: so.Mapped[int] = so.mapped_column(sa.Integer, primary_key=True, default=0)

    phenotype: so.Mapped[Phenotype] = so.relationship(
        "Phenotype",
        back_populates="phenotype_definitions",
        foreign_keys=[phenotype_id],
    )

    concept: so.Mapped[Concept] = so.relationship(
        "Concept",
        primaryjoin=lambda: so.foreign(PhenotypeDefinition.query_concept_id) == Concept.concept_id,
        foreign_keys=[query_concept_id],
    )

    def _html_title(self) -> str:
        return "Phenotype Definition"

    def _html_header(self) -> dict[str, str]:
        return {
            "Concept ID": f'{self.query_concept_id}',
            "Concept name": self.concept.concept_name if self.concept else "",
            "Vocabulary": self.concept.vocabulary_id if self.concept else "",
        }

    def _html_inner(self):
        return []
