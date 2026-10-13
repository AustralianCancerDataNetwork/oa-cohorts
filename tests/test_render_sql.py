import sqlalchemy as sa
from omop_alchemy.cdm.model import Concept

from oa_cohorts.core.html_utils import render_sql


def test_render_sql_uses_the_supplied_schema_translate_map() -> None:
    engine = sa.create_engine(
        "sqlite://",
        execution_options={
            "schema_translate_map": {
                "primary": "clinical_test",
                "vocab": "vocabulary_test",
            }
        },
    )

    sql = render_sql(
        sa.select(Concept.concept_id),
        engine.get_execution_options()["schema_translate_map"],
    )

    assert "vocabulary_test.concept.concept_id" in sql
