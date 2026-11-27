from decimal import Decimal

import numpy as np
import pytest

from oa_cohorts.core.coercion import _normalise_null


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (float("nan"), None),
        (np.float32("nan"), None),
        ("", None),
        ("  NaN  ", None),
        ("NULL", None),
        (" None ", None),
        ("NA", None),
        (" n/a ", None),
        ("0", "0"),
        ("value", "value"),
        (0, 0),
        (Decimal("NaN"), None),
    ],
)
def test_normalise_null(value, expected):
    assert _normalise_null(value) == expected
