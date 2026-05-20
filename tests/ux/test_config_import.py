from __future__ import annotations

import csv
from pathlib import Path

import sqlalchemy as sa
import sqlalchemy.orm as so
from typer.testing import CliRunner

from oa_cohorts.cli import app, main
from oa_cohorts.cli.config_import import (
    CONFIG_IMPORT_SPECS,
    _clean_row,
    import_config_directory,
)
from orm_loader.helpers import Base


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _build_config_dir(config_dir: Path) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)

    _write_csv(
        config_dir / "phenotype.csv",
        ["phenotype_id", "phenotype_name", "description"],
        [
            {"phenotype_id": 1, "phenotype_name": "nsclc", "description": ""},
        ],
    )
    _write_csv(
        config_dir / "phenotype_definition.csv",
        ["phenotype_id", "query_concept_id"],
        [
            {"phenotype_id": 1, "query_concept_id": 36561408},
            {"phenotype_id": 1, "query_concept_id": 44500480},
        ],
    )
    _write_csv(
        config_dir / "query_rule.csv",
        [
            "query_rule_id",
            "matcher",
            "concept_id",
            "notes",
            "scalar_threshold",
            "threshold_direction",
            "threshold_comparator",
            "phenotype_id",
        ],
        [
            {
                "query_rule_id": 1,
                "matcher": "exact",
                "concept_id": 36561408,
                "notes": "",
                "scalar_threshold": "",
                "threshold_direction": "",
                "threshold_comparator": "",
                "phenotype_id": "",
            },
            {
                "query_rule_id": 2,
                "matcher": "phenotype",
                "concept_id": "",
                "notes": "",
                "scalar_threshold": "",
                "threshold_direction": "",
                "threshold_comparator": "",
                "phenotype_id": 1,
            },
        ],
    )
    _write_csv(
        config_dir / "subquery.csv",
        ["subquery_id", "target", "temporality", "name", "short_name"],
        [
            {
                "subquery_id": 1,
                "target": "dx_primary",
                "temporality": "dt_current_start",
                "name": "Primary diagnosis",
                "short_name": "primary_dx",
            },
            {
                "subquery_id": 2,
                "target": "dx_primary",
                "temporality": "dt_current_start",
                "name": "Phenotype diagnosis",
                "short_name": "phen_dx",
            },
        ],
    )
    _write_csv(
        config_dir / "subquery_rule_map.csv",
        ["subquery_id", "query_rule_id"],
        [
            {"subquery_id": 1, "query_rule_id": 1},
            {"subquery_id": 1, "query_rule_id": 1},
            {"subquery_id": 2, "query_rule_id": 2},
        ],
    )
    _write_csv(
        config_dir / "measure.csv",
        ["measure_id", "name", "combination", "subquery_id", "person_ep_override"],
        [
            {
                "measure_id": 1,
                "name": "Diagnosis",
                "combination": "or",
                "subquery_id": 1,
                "person_ep_override": False,
            },
            {
                "measure_id": 2,
                "name": "Phenotype measure",
                "combination": "or",
                "subquery_id": 2,
                "person_ep_override": False,
            },
            {
                "measure_id": 3,
                "name": "Composite",
                "combination": "and",
                "subquery_id": "",
                "person_ep_override": False,
            },
        ],
    )
    _write_csv(
        config_dir / "measure_relationship.csv",
        ["parent_measure_id", "child_measure_id"],
        [
            {"parent_measure_id": 3, "child_measure_id": 1},
            {"parent_measure_id": 3, "child_measure_id": 2},
            {"parent_measure_id": 3, "child_measure_id": 2},
        ],
    )
    _write_csv(
        config_dir / "measure_temporal_window.csv",
        [
            "measure_id",
            "candidate_measure_id",
            "window_min_days",
            "window_max_days",
            "window_pick_strategy",
            "result_date_source",
            "require_same_resolver",
        ],
        [],
    )
    _write_csv(
        config_dir / "dash_cohort_def.csv",
        ["dash_cohort_def_id", "dash_cohort_def_name", "dash_cohort_def_short_name", "measure_id"],
        [
            {
                "dash_cohort_def_id": 1,
                "dash_cohort_def_name": "Base cohort",
                "dash_cohort_def_short_name": "base",
                "measure_id": 3,
            },
        ],
    )
    _write_csv(
        config_dir / "dash_cohort.csv",
        ["dash_cohort_id", "dash_cohort_name"],
        [
            {"dash_cohort_id": 1, "dash_cohort_name": "Test cohort"},
        ],
    )
    _write_csv(
        config_dir / "dash_cohort_def_map.csv",
        ["dash_cohort_def_id", "dash_cohort_id"],
        [
            {"dash_cohort_def_id": 1, "dash_cohort_id": 1},
            {"dash_cohort_def_id": 1, "dash_cohort_id": 1},
        ],
    )
    _write_csv(
        config_dir / "indicator.csv",
        [
            "indicator_id",
            "indicator_description",
            "indicator_reference",
            "numerator_measure_id",
            "denominator_measure_id",
            "benchmark",
            "benchmark_unit",
        ],
        [
            {
                "indicator_id": 1,
                "indicator_description": "Test indicator",
                "indicator_reference": "",
                "numerator_measure_id": 2,
                "denominator_measure_id": 1,
                "benchmark": "",
                "benchmark_unit": "days",
            },
        ],
    )
    _write_csv(
        config_dir / "report.csv",
        [
            "report_id",
            "report_name",
            "report_short_name",
            "report_description",
            "report_create_date",
            "report_edit_date",
            "report_author",
            "report_owner",
        ],
        [
            {
                "report_id": 1,
                "report_name": "Test report",
                "report_short_name": "test",
                "report_description": "Initial description",
                "report_create_date": "2024-05-15",
                "report_edit_date": "2024-05-15",
                "report_author": "Author",
                "report_owner": "",
            },
        ],
    )
    _write_csv(
        config_dir / "report_cohort_map.csv",
        ["report_cohort_map_id", "report_id", "dash_cohort_id", "primary_cohort"],
        [
            {
                "report_cohort_map_id": 1,
                "report_id": 1,
                "dash_cohort_id": 1,
                "primary_cohort": True,
            },
        ],
    )
    _write_csv(
        config_dir / "report_indicator_map.csv",
        ["report_id", "indicator_id"],
        [
            {"report_id": 1, "indicator_id": 1},
            {"report_id": 1, "indicator_id": 1},
        ],
    )
    return config_dir


def _result_lookup(results):
    return {result.table_name: result for result in results}


def _table_count(session: so.Session, table: sa.Table) -> int:
    return session.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()


def test_import_config_directory_deduplicates_and_loads_rows(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    engine = sa.create_engine("sqlite://")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        results = import_config_directory(config_dir, session)
        by_table = _result_lookup(results)

        assert by_table["subquery_rule_map"].dropped_duplicate_rows == 1
        assert by_table["dash_cohort_def_map"].dropped_duplicate_rows == 1
        assert by_table["report_indicator_map"].dropped_duplicate_rows == 1
        assert by_table["measure_relationship"].dropped_duplicate_rows == 1

        assert _table_count(session, Base.metadata.tables["subquery_rule_map"]) == 2
        assert _table_count(session, Base.metadata.tables["dash_cohort_def_map"]) == 1
        assert _table_count(session, Base.metadata.tables["report_indicator_map"]) == 1
        assert _table_count(session, Base.metadata.tables["measure_relationship"]) == 2


def test_import_config_directory_tolerates_unresolved_concept_references(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    engine = sa.create_engine("sqlite://")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        results = import_config_directory(config_dir, session)
        by_table = _result_lookup(results)

        assert by_table["query_rule"].inserted_rows == 2
        assert by_table["phenotype_definition"].inserted_rows == 2

        query_rule_table = Base.metadata.tables["query_rule"]
        phenotype_definition_table = Base.metadata.tables["phenotype_definition"]

        query_rule_concepts = {
            row["concept_id"]
            for row in session.execute(sa.select(query_rule_table)).mappings()
            if row["concept_id"] is not None
        }
        phenotype_concepts = {
            row["query_concept_id"]
            for row in session.execute(sa.select(phenotype_definition_table)).mappings()
        }

        assert query_rule_concepts == {36561408}
        assert phenotype_concepts == {36561408, 44500480}

    inspector = sa.inspect(engine)
    assert inspector.has_table("concept") is False


def test_reimport_skips_existing_and_replaces_changed_rows(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    engine = sa.create_engine("sqlite://")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        import_config_directory(config_dir, session)

    _write_csv(
        config_dir / "report.csv",
        [
            "report_id",
            "report_name",
            "report_short_name",
            "report_description",
            "report_create_date",
            "report_edit_date",
            "report_author",
            "report_owner",
        ],
        [
            {
                "report_id": 1,
                "report_name": "Test report",
                "report_short_name": "test",
                "report_description": "Updated description",
                "report_create_date": "2024-05-15",
                "report_edit_date": "2024-05-16",
                "report_author": "Author",
                "report_owner": "Owner",
            },
        ],
    )

    with session_factory() as session:
        results = import_config_directory(config_dir, session, create_tables=False)
        by_table = _result_lookup(results)

        assert by_table["query_rule"].skipped_existing_rows == 2
        assert by_table["report"].replaced_rows == 1
        assert by_table["report"].inserted_rows == 0

        report_table = Base.metadata.tables["report"]
        row = session.execute(sa.select(report_table)).mappings().one()
        assert row["report_description"] == "Updated description"
        assert row["report_owner"] == "Owner"


def test_dry_run_returns_counts_without_writing(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    engine = sa.create_engine("sqlite://")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        results = import_config_directory(config_dir, session, dry_run=True)
        by_table = _result_lookup(results)

        assert by_table["phenotype"].inserted_rows == 1
        assert by_table["subquery_rule_map"].dropped_duplicate_rows == 1

    inspector = sa.inspect(engine)
    assert inspector.has_table("phenotype") is False
    assert inspector.has_table("query_rule") is False


def test_progress_callback_emits_deterministic_events_and_skips_write_on_dry_run(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    engine = sa.create_engine("sqlite://")
    session_factory = so.sessionmaker(bind=engine, future=True)
    events = []

    with session_factory() as session:
        import_config_directory(
            config_dir,
            session,
            dry_run=True,
            progress_callback=events.append,
        )

    phases = [event.phase for event in events]
    assert phases[0] == "start"
    assert phases[-1] == "complete"
    assert "write" not in phases

    phenotype_events = [event.phase for event in events if event.table_name == "phenotype"]
    assert phenotype_events == ["load", "dedupe", "compare", "complete"]


def test_clean_row_supports_current_columns_and_values():
    measure_spec = next(spec for spec in CONFIG_IMPORT_SPECS if spec.table.name == "measure")
    subquery_spec = next(spec for spec in CONFIG_IMPORT_SPECS if spec.table.name == "subquery")
    query_rule_map_spec = next(
        spec for spec in CONFIG_IMPORT_SPECS if spec.table.name == "subquery_rule_map"
    )

    measure_row = _clean_row(
        {
            "measure_id": "55",
            "name": "Bronchial Cancer - SNOMED",
            "combination": "or",
            "subquery_id": "123",
            "person_ep_override": "t",
        },
        measure_spec,
    )
    subquery_row = _clean_row(
        {
            "subquery_id": "85",
            "name": "Curative RT",
            "short_name": "curative_rt",
            "temporality": "dt_rad",
            "target": "intent_rt",
        },
        subquery_spec,
    )

    assert measure_row["combination"].value == "or"
    assert measure_row["person_ep_override"] is True
    assert subquery_row["name"] == "Curative RT"
    assert subquery_row["target"].value == "intent_rt"
    assert query_rule_map_spec.filenames == ("subquery_rule_map.csv",)


def test_cli_main_imports_configs(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    database_path = tmp_path / "config.db"
    status = main(
        [
            "import-config",
            str(config_dir),
            "--database-url",
            f"sqlite:///{database_path}",
        ]
    )
    assert status == 0


def test_cli_main_dry_run_returns_zero(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    database_path = tmp_path / "config.db"
    status = main(
        [
            "import-config",
            str(config_dir),
            "--database-url",
            f"sqlite:///{database_path}",
            "--dry-run",
        ]
    )
    assert status == 0


def test_cli_main_failure_returns_non_zero(tmp_path):
    status = main(
        [
            "import-config",
            str(tmp_path / "missing-config"),
            "--dry-run",
        ]
    )
    assert status == 1


def test_report_summary_cli_prints_existing_report_details(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    database_path = tmp_path / "config.db"
    engine = sa.create_engine(f"sqlite:///{database_path}")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        import_config_directory(config_dir, session)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "report-summary",
            "--database-url",
            f"sqlite:///{database_path}",
        ],
    )

    assert result.exit_code == 0
    assert "Report Summary" in result.stdout
    assert "Test report" in result.stdout
    assert "test" in result.stdout
    assert "Test cohort" in result.stdout


def test_report_summary_cli_reports_when_no_matching_report_exists(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    database_path = tmp_path / "config.db"
    engine = sa.create_engine(f"sqlite:///{database_path}")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        import_config_directory(config_dir, session)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "report-summary",
            "--database-url",
            f"sqlite:///{database_path}",
            "--short-name",
            "missing",
        ],
    )

    assert result.exit_code == 0
    assert "No matching reports were found." in result.stdout


def test_report_summary_cli_reports_when_schema_has_not_been_loaded(tmp_path):
    database_path = tmp_path / "empty.db"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "report-summary",
            "--database-url",
            f"sqlite:///{database_path}",
        ],
    )

    assert result.exit_code == 0
    assert "The report table is not available in this database yet." in result.stdout
    assert "import-config" in result.stdout


def test_bootstrap_schema_cli_creates_query_tables(tmp_path):
    database_path = tmp_path / "bootstrap.db"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "bootstrap-schema",
            "--database-url",
            f"sqlite:///{database_path}",
        ],
    )

    assert result.exit_code == 0
    assert "Schema Bootstrap" in result.stdout
    assert "Created now" in result.stdout

    engine = sa.create_engine(f"sqlite:///{database_path}")
    inspector = sa.inspect(engine)
    assert inspector.has_table("report") is True
    assert inspector.has_table("query_rule") is True
    assert inspector.has_table("indicator") is True


def test_indicator_summary_cli_prints_indicator_details(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    database_path = tmp_path / "config.db"
    engine = sa.create_engine(f"sqlite:///{database_path}")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        import_config_directory(config_dir, session)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "indicator-summary",
            "1",
            "--database-url",
            f"sqlite:///{database_path}",
        ],
    )

    assert result.exit_code == 0
    assert "Indicator Summary" in result.stdout
    assert "Test indicator" in result.stdout
    assert "Test report (test)" in result.stdout
    assert "2: Phenotype measure" in result.stdout
    assert "1: Diagnosis" in result.stdout
    assert "Temporality: dt_current_start" in result.stdout


def test_indicator_summary_cli_reports_missing_indicator(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    database_path = tmp_path / "config.db"
    engine = sa.create_engine(f"sqlite:///{database_path}")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        import_config_directory(config_dir, session)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "indicator-summary",
            "99",
            "--database-url",
            f"sqlite:///{database_path}",
        ],
    )

    assert result.exit_code == 0
    assert "No indicator was found for indicator_id=99." in result.stdout


def test_report_indicator_summary_cli_prints_report_indicators(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    database_path = tmp_path / "config.db"
    engine = sa.create_engine(f"sqlite:///{database_path}")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        import_config_directory(config_dir, session)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "report-indicator-summary",
            "1",
            "--database-url",
            f"sqlite:///{database_path}",
        ],
    )

    assert result.exit_code == 0
    assert "Indicator Summary: Test report (test)" in result.stdout
    assert "Test indicator" in result.stdout
    assert "2: Phenotype measure" in result.stdout
    assert "1: Diagnosis" in result.stdout


def test_report_indicator_summary_cli_reports_missing_report(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    database_path = tmp_path / "config.db"
    engine = sa.create_engine(f"sqlite:///{database_path}")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        import_config_directory(config_dir, session)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "report-indicator-summary",
            "99",
            "--database-url",
            f"sqlite:///{database_path}",
        ],
    )

    assert result.exit_code == 0
    assert "No report was found for report_id=99." in result.stdout


def test_measure_summary_cli_prints_measure_details(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    database_path = tmp_path / "config.db"
    engine = sa.create_engine(f"sqlite:///{database_path}")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        import_config_directory(config_dir, session)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "measure-summary",
            "1",
            "--database-url",
            f"sqlite:///{database_path}",
        ],
    )

    assert result.exit_code == 0
    assert "Measure Summary" in result.stdout
    assert "Diagnosis" in result.stdout
    assert "Kind" in result.stdout
    assert "leaf" in result.stdout
    assert "Primary diagnosis" in result.stdout
    assert "Target: dx_primary" in result.stdout
    assert "1: Test indicator [Test report (test)]" in result.stdout


def test_measure_summary_cli_reports_missing_measure(tmp_path):
    config_dir = _build_config_dir(tmp_path / "config")
    database_path = tmp_path / "config.db"
    engine = sa.create_engine(f"sqlite:///{database_path}")
    session_factory = so.sessionmaker(bind=engine, future=True)

    with session_factory() as session:
        import_config_directory(config_dir, session)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "measure-summary",
            "99",
            "--database-url",
            f"sqlite:///{database_path}",
        ],
    )

    assert result.exit_code == 0
    assert "No measure was found for measure_id=99." in result.stdout
