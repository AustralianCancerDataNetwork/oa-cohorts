from datetime import date
from typing import cast

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import (
    CDMDatabaseConfig,
    ConnectionConfig,
    ResolvedCDMDatabase,
    Resolver,
    StackConfig,
)
from omop_alchemy.cdm.model import Concept, Concept_Ancestor
from omop_alchemy.config import create_cdm_engines
from omop_constructs.config import require_colocated_vocabulary

from oa_cohorts.core import RuleCombination, RuleTarget, RuleTemporality
from oa_cohorts.query.measure import Measure, MeasureSQLCompiler
from oa_cohorts.query.query_rule import HierarchyRule, QueryRule
from oa_cohorts.query.subquery import Subquery


def test_hierarchy_rule_compiles_through_role_tagged_ancestor_model():
    stack = StackConfig.for_session(
        connections={"local": ConnectionConfig(dialect="sqlite", database_name=":memory:")},
        databases={"cdm_db": CDMDatabaseConfig(connection="local")},
    )
    resolved = cast(ResolvedCDMDatabase, Resolver(stack).resolve_database("cdm_db"))
    require_colocated_vocabulary(resolved)
    primary, vocab = create_cdm_engines(resolved, register_claims=False)
    try:
        with primary.begin() as connection:
            concept_table = cast(sa.Table, Concept.__table__)
            ancestor_table = cast(sa.Table, Concept_Ancestor.__table__)
            query_rule_table = cast(sa.Table, QueryRule.__table__)
            concept_table.create(connection)
            ancestor_table.create(connection)
            query_rule_table.create(connection)
            connection.execute(
                concept_table.insert(),
                [
                    {"concept_id": 101, "concept_name": "parent", "domain_id": "Condition",
                     "vocabulary_id": "Test", "concept_class_id": "Clinical Finding",
                     "concept_code": "101", "valid_start_date": date(2000, 1, 1),
                     "valid_end_date": date(2099, 12, 31)},
                    {"concept_id": 102, "concept_name": "child", "domain_id": "Condition",
                     "vocabulary_id": "Test", "concept_class_id": "Clinical Finding",
                     "concept_code": "102", "valid_start_date": date(2000, 1, 1),
                     "valid_end_date": date(2099, 12, 31)},
                ],
            )
            connection.execute(
                ancestor_table.insert(),
                [
                    {"ancestor_concept_id": 101, "descendant_concept_id": 101,
                     "min_levels_of_separation": 0, "max_levels_of_separation": 0},
                    {"ancestor_concept_id": 101, "descendant_concept_id": 102,
                     "min_levels_of_separation": 1, "max_levels_of_separation": 1},
                ],
            )
        with so.Session(primary) as session:
            rule = HierarchyRule(query_rule_id=1, concept_id=101)
            session.add(rule)
            session.flush()
            session.refresh(rule, ["concept"])
            assert [concept.concept_id for concept in rule.children] == [101, 102]

        subquery = Subquery(
            subquery_id=1,
            target=RuleTarget.dx_any,
            temporality=RuleTemporality.dt_any,
            rules=[rule],
        )
        measure = Measure(
            measure_id=1,
            name="parent hierarchy",
            combination=RuleCombination.rule_or,
            subquery=subquery,
        )
        statement = MeasureSQLCompiler(measure).sql_any()
        compiled = str(statement.compile(bind=primary, compile_kwargs={"literal_binds": True}))
    finally:
        if vocab is not primary:
            vocab.dispose()
        primary.dispose()

    # The self-row puts the parent itself among the hierarchy matches.
    assert "concept_id IN (101, 102)" in compiled
