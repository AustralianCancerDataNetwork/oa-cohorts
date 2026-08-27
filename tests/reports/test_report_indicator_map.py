"""The report-indicator link as a mapped class.

`report_indicator_map` used to be a bare `sa.Table`, so a report had nowhere to record
its own view of a shared indicator. It is now a mapped class carrying per-report overrides,
which means one thing has to stay true: only one relationship may write the table.
"""

from __future__ import annotations

import warnings

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so
from sqlalchemy.exc import SAWarning

from oa_cohorts.query.indicator import Indicator
from oa_cohorts.query.measure import Measure
from oa_cohorts.query.report import Report, ReportIndicatorMap
from oa_cohorts.schema import create_owned_tables

OVERRIDE_COLUMNS = (
    "indicator_label_override",
    "indicator_reference_override",
    "benchmark_override",
    "benchmark_unit_override",
)


@pytest.fixture
def session(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'links.db'}")
    create_owned_tables(engine)
    factory = so.sessionmaker(bind=engine, future=True)
    with factory() as session:
        yield session
    engine.dispose()


def _seed(session: so.Session) -> None:
    """One generalisable indicator shared by two reports -- the shape that motivated this."""
    session.add(Measure(measure_id=0, name="Full report cohort", combination="or"))
    session.add(
        Indicator(
            indicator_id=1,
            indicator_description="Presented at MDT meeting",
            indicator_reference=None,
            numerator_measure_id=0,
            denominator_measure_id=0,
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


# --------------------------------------------------------------------------
# One writer
# --------------------------------------------------------------------------


def test_the_secondary_relationships_are_viewonly():
    """Two writable paths onto one table can disagree about what was written.

    Every existing reader of these two relationships was left alone; writes go through
    the link class or the table.
    """
    assert Report.indicators.property.viewonly
    assert Indicator.in_reports.property.viewonly


def test_the_link_relationships_are_writable():
    assert not Report.indicator_links.property.viewonly
    assert not Indicator.report_links.property.viewonly


def test_mappers_configure_without_overlap_warnings():
    """A mapped class over a table that is also someone's ``secondary`` warns unless the
    secondary side is viewonly. The warning is the signal that writes could conflict."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        so.configure_mappers()


# --------------------------------------------------------------------------
# Core statements against the mapped class
# --------------------------------------------------------------------------


def test_the_class_names_the_link_table():
    """There is one name for this table.

    A module-level ``report_indicator_map = ReportIndicatorMap.__table__`` alias existed
    briefly so statements written against the bare table kept working. It was removed before
    1.0 rather than carried as a shim; call sites use the class, or ``__table__`` where a
    Table object is genuinely required.
    """
    assert ReportIndicatorMap.__tablename__ == "report_indicator_map"
    assert ReportIndicatorMap.__table__.name == "report_indicator_map"


def test_a_core_insert_against_the_class_still_links(session):
    """Core insert and delete take the mapped class, as ReportCohortMap already did."""
    _seed(session)

    session.execute(sa.insert(ReportIndicatorMap).values(report_id=1, indicator_id=1))
    session.commit()
    session.expire_all()

    assert [i.indicator_id for i in session.get(Report, 1).indicators] == [1]


def test_a_core_delete_against_the_class_unlinks(session):
    _seed(session)
    session.add(ReportIndicatorMap(report_id=1, indicator_id=1))
    session.commit()

    session.execute(sa.delete(ReportIndicatorMap).where(ReportIndicatorMap.report_id == 1))
    session.commit()
    session.expire_all()

    assert session.get(Report, 1).indicators == []


# --------------------------------------------------------------------------
# Overrides
# --------------------------------------------------------------------------


def test_a_link_defaults_to_inheriting(session):
    _seed(session)
    session.add(ReportIndicatorMap(report_id=1, indicator_id=1))
    session.commit()
    session.expire_all()

    link = session.get(ReportIndicatorMap, (1, 1))

    assert [getattr(link, name) for name in OVERRIDE_COLUMNS] == [None] * len(OVERRIDE_COLUMNS)


def test_two_reports_can_hold_different_overrides_for_one_indicator(session):
    """The whole point: one indicator, one canonical description, two presentations."""
    _seed(session)
    session.add(
        ReportIndicatorMap(
            report_id=1,
            indicator_id=1,
            indicator_label_override="Presented at Lung MDT meeting",
            indicator_reference_override="LUCAP 3.1",
            benchmark_override=85,
            benchmark_unit_override="percent",
        )
    )
    session.add(
        ReportIndicatorMap(
            report_id=2,
            indicator_id=1,
            indicator_label_override="Presented at Head & Neck MDT meeting",
        )
    )
    session.commit()
    session.expire_all()

    lung = session.get(ReportIndicatorMap, (1, 1))
    hnc = session.get(ReportIndicatorMap, (2, 1))

    assert lung.indicator_label_override == "Presented at Lung MDT meeting"
    assert lung.indicator_reference_override == "LUCAP 3.1"
    assert hnc.indicator_label_override == "Presented at Head & Neck MDT meeting"
    # Overrides are per field: H&N restated the label and inherits the rest.
    assert hnc.indicator_reference_override is None
    assert hnc.benchmark_override is None
    # And the indicator itself stayed generalisable.
    assert lung.indicator.indicator_description == "Presented at MDT meeting"
    assert lung.indicator is hnc.indicator


def test_both_sides_navigate_to_the_link(session):
    _seed(session)
    session.add(ReportIndicatorMap(report_id=1, indicator_id=1))
    session.add(ReportIndicatorMap(report_id=2, indicator_id=1))
    session.commit()
    session.expire_all()

    report_links = session.get(Report, 1).indicator_links
    indicator_links = session.get(Indicator, 1).report_links

    assert [link.indicator_id for link in report_links] == [1]
    assert sorted(link.report_id for link in indicator_links) == [1, 2]


def test_a_link_is_unique_per_report_and_indicator(session):
    """The composite primary key is the constraint that stops two overrides for one pair."""
    _seed(session)
    session.add(ReportIndicatorMap(report_id=1, indicator_id=1))
    session.commit()

    session.add(ReportIndicatorMap(report_id=1, indicator_id=1, benchmark_override=85))
    with pytest.raises(sa.exc.IntegrityError):
        session.commit()
