from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from oa_cohorts.errors import MissingCdmTableError
from oa_cohorts.output import report_runner as report_runner_module
from oa_cohorts.output.report_runner import ReportRunner
from tests.db_error_helpers import fake_undefined_table_error


class _RaisingDb:
    def execute(self, stmt):
        raise fake_undefined_table_error("person")


class _FakeDemographyFilter:
    def to_rows_stmt(self, *, restrict_to_person_ids):
        # A trivial stand-in statement: this test verifies collect_demography's
        # own error wrapping, not DemographyFilter/PersonDemography (the real
        # ones pull in the full omop_constructs CDM mapping, which is unrelated
        # to what's under test here).
        return sa.select(sa.literal(1))


def test_collect_demography_raises_missing_cdm_table_error(monkeypatch):
    monkeypatch.setattr(report_runner_module, "DemographyFilter", _FakeDemographyFilter)

    report = SimpleNamespace(report_short_name="Test Report", cohorts=[])
    runner = ReportRunner(_RaisingDb(), report)

    with pytest.raises(MissingCdmTableError, match="Test Report"):
        runner.collect_demography(strict=False)
