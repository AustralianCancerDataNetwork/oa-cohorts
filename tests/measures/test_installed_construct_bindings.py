"""Exercise the real construct classes without a vocabulary database."""

import subprocess
import sys
from textwrap import dedent


def test_installed_constructs_bind_every_measurable():
    result = subprocess.run(
        [sys.executable, "-c", dedent("""\
            from collections import defaultdict
            from types import SimpleNamespace

            from omop_constructs import semantics

            # The actual ORM classes and columns must come from the installed
            # package. Only the vocabulary lookup is replaced.
            semantics.registry = defaultdict(lambda: SimpleNamespace(all_concepts=()))

            from oa_cohorts.core import RuleTarget
            from oa_cohorts.measurables import get_measurable_registry

            registry = get_measurable_registry()
            assert registry[RuleTarget.tx_concurrent].__bound_measurable__.event_date_col.key == "first_concurrent_rt_start"
            assert registry[RuleTarget.obs_value_concept].__bound_measurable__.value_concept_col.key == "value_concept_id"
        """)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
