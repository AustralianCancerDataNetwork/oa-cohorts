"""Resolution of per-report indicator labels, references and benchmarks.

One indicator, one canonical description, two reports that state it differently. These
tests are the ones that would have caught the original defect: "Presented at Lung MDT
meeting" appearing verbatim in the head and neck report.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so

from oa_cohorts.cli.indicator_summary import (
    load_indicator_detail_summary,
    load_indicator_summaries,
)
from oa_cohorts.cli.measure_summary import load_measure_detail_summary
from oa_cohorts.output.pivot_queries import build_report_payload
from oa_cohorts.query.indicator import Indicator
from oa_cohorts.query.measure import Measure
from oa_cohorts.query.report import Report, ReportIndicatorMap
from oa_cohorts.schema import create_owned_tables

CANONICAL = "Presented at MDT meeting"
LUNG_LABEL = "Presented at Lung MDT meeting"


@pytest.fixture
def session(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'labels.db'}")
    create_owned_tables(engine)
    factory = so.sessionmaker(bind=engine, future=True)
    with factory() as session:
        _seed(session)
        yield session
    engine.dispose()


def _seed(session: so.Session) -> None:
    """One generalisable indicator in two reports.

    The lung report restates all four fields; head and neck inherits every one. The
    indicator itself carries no lung wording and no LUCAP citation -- that is the point.
    """
    session.add(Measure(measure_id=0, name="Full report cohort", combination="or"))
    session.add(Measure(measure_id=33, name="Discussed at MDT", combination="or"))
    session.add(
        Indicator(
            indicator_id=1,
            indicator_description=CANONICAL,
            indicator_reference=None,
            numerator_measure_id=33,
            denominator_measure_id=0,
            benchmark=None,
            benchmark_unit=None,
        )
    )
    for report_id, short_name, name in (
        (1, "lung_mdt", "Lung Cancer MDT"),
        (2, "hnc", "Head & Neck Cancer"),
    ):
        session.add(
            Report(
                report_id=report_id,
                report_name=name,
                report_short_name=short_name,
                report_description="d",
                report_author="GK",
            )
        )
    session.flush()
    session.add(
        ReportIndicatorMap(
            report_id=1,
            indicator_id=1,
            indicator_label_override=LUNG_LABEL,
            indicator_reference_override="LUCAP 3.1",
            benchmark_override=85,
            benchmark_unit_override="percent",
        )
    )
    session.add(ReportIndicatorMap(report_id=2, indicator_id=1))
    session.commit()


# --------------------------------------------------------------------------
# The model accessors
# --------------------------------------------------------------------------


def test_the_same_indicator_reads_differently_in_each_report(session):
    indicator = session.get(Indicator, 1)
    lung = session.get(Report, 1)
    hnc = session.get(Report, 2)

    assert indicator.label_in(lung) == LUNG_LABEL
    assert indicator.label_in(hnc) == CANONICAL
    assert indicator.indicator_description == CANONICAL


def test_a_reference_does_not_leak_into_a_report_that_did_not_claim_it(session):
    """LUCAP is the lung standard. Held on the lung link, it cannot reach head and neck."""
    indicator = session.get(Indicator, 1)

    assert indicator.reference_in(session.get(Report, 1)) == "LUCAP 3.1"
    assert indicator.reference_in(session.get(Report, 2)) is None


def test_benchmarks_resolve_per_report(session):
    indicator = session.get(Indicator, 1)

    assert indicator.benchmark_in(session.get(Report, 1)) == (85, "percent")
    assert indicator.benchmark_in(session.get(Report, 2)) == (None, None)


def test_resolution_is_per_field(session):
    """A report can restate the reference while inheriting the label, and vice versa."""
    link = session.get(ReportIndicatorMap, (2, 1))
    link.indicator_reference_override = "HNCAP 1.2"
    session.commit()

    assert link.label == CANONICAL
    assert link.reference == "HNCAP 1.2"


def test_an_indicator_outside_a_report_falls_back_to_canonical(session):
    """label_in must not invent a link for a report the indicator is not in."""
    session.add(
        Report(
            report_id=3,
            report_name="Colorectal Cancer",
            report_short_name="crc",
            report_description="d",
            report_author="GK",
        )
    )
    session.commit()
    indicator = session.get(Indicator, 1)

    assert indicator.link_for(session.get(Report, 3)) is None
    assert indicator.label_in(session.get(Report, 3)) == CANONICAL


# --------------------------------------------------------------------------
# The payload
# --------------------------------------------------------------------------


def test_the_payload_carries_canonical_and_display_values(session):
    """Downstream keys off the canonical fields and renders the display ones.

    ``executor=None`` is safe here only because neither report has a cohort, so nothing
    asks the executor for members. Label resolution never touches it.
    """
    lung = build_report_payload(session.get(Report, 1), executor=None)
    hnc = build_report_payload(session.get(Report, 2), executor=None)

    (lung_ind,) = lung.indicators
    (hnc_ind,) = hnc.indicators

    # Same indicator, by identity.
    assert lung_ind.indicator_id == hnc_ind.indicator_id == 1
    assert lung_ind.indicator_description == hnc_ind.indicator_description == CANONICAL

    # Different presentation.
    assert lung_ind.display_label == LUNG_LABEL
    assert hnc_ind.display_label == CANONICAL
    assert lung_ind.display_reference == "LUCAP 3.1"
    assert hnc_ind.display_reference is None
    assert (lung_ind.display_benchmark, lung_ind.display_benchmark_unit) == (85, "percent")
    assert (hnc_ind.display_benchmark, hnc_ind.display_benchmark_unit) == (None, None)


def test_the_payload_display_fields_equal_canonical_when_nothing_is_overridden(session):
    hnc = build_report_payload(session.get(Report, 2), executor=None)
    (indicator,) = hnc.indicators

    assert indicator.display_label == indicator.indicator_description
    assert indicator.display_reference == indicator.indicator_reference


# --------------------------------------------------------------------------
# The CLI views
# --------------------------------------------------------------------------


def test_the_report_scoped_list_view_shows_the_report_wording(session):
    lung = load_indicator_summaries(session, report_id=1)
    hnc = load_indicator_summaries(session, report_id=2)

    assert [s.description for s in lung] == [LUNG_LABEL]
    assert [s.reference for s in lung] == ["LUCAP 3.1"]
    assert [s.benchmark_summary for s in lung] == ["85 percent"]

    assert [s.description for s in hnc] == [CANONICAL]
    assert [s.reference for s in hnc] == [None]
    assert [s.benchmark_summary for s in hnc] == ["-"]


def test_the_detail_view_shows_canonical_plus_every_report_presentation(session):
    """This view spans reports, so it cannot resolve one label -- it lists them all."""
    detail = load_indicator_detail_summary(session, indicator_id=1)

    assert detail.description == CANONICAL
    assert detail.reference is None

    by_short_name = {p.report_short_name: p for p in detail.presentations}
    assert set(by_short_name) == {"lung_mdt", "hnc"}
    assert by_short_name["lung_mdt"].label == LUNG_LABEL
    assert by_short_name["lung_mdt"].overridden == (
        "label",
        "reference",
        "benchmark",
        "benchmark unit",
    )
    assert by_short_name["hnc"].label == CANONICAL
    assert by_short_name["hnc"].overridden == ()


def test_measure_usage_names_the_report_that_restates_the_label(session):
    """A measure-centric view leads with the canonical name, so a restatement must say so
    or the reader hunts for wording that appears in no report."""
    detail = load_measure_detail_summary(session, measure_id=33)

    (usage,) = detail.numerator_indicator_usages
    assert CANONICAL in usage
    assert f'Lung Cancer MDT (lung_mdt) as "{LUNG_LABEL}"' in usage
    assert "Head & Neck Cancer (hnc)" in usage
