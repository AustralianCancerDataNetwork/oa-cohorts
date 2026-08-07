import pytest
import sqlalchemy as sa


def test_exact_rule_missing_concept(rule_exact_missing):
    with pytest.raises(RuntimeError, match="Rule concept"):
        rule_exact_missing.get_filter_details(sa.column("x"))

def test_scalar_rule_missing_threshold(rule_scalar_missing):
    with pytest.raises(RuntimeError, match="Scalar threshold"):
        rule_scalar_missing.get_filter_details(sa.column("x"))

def test_scalar_rule_missing_comparator(rule_scalar_missing_comparator):
    with pytest.raises(RuntimeError, match="Threshold comparator"):
        rule_scalar_missing_comparator.get_filter_details(sa.column("x"))

def test_phenotype_rule_missing(rule_phenotype_missing):
    with pytest.raises(RuntimeError, match="Rule phenotype"):
        rule_phenotype_missing.get_filter_details(sa.column("x"))