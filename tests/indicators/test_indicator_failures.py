import pytest


def test_indicator_numerator_without_execution(indicator, executor):
    with pytest.raises(RuntimeError):
        indicator.numerator_members(executor)

def test_indicator_denominator_without_execution(indicator, executor):
    with pytest.raises(RuntimeError):
        indicator.denominator_members(executor)