from __future__ import annotations

from pathlib import Path
from typing import Annotated, Sequence

import typer
from rich.console import Console
import sqlalchemy as sa
import sqlalchemy.orm as so
from typer.main import get_command

from oa_configurator import load_stack_config
from ..config import OaCohortsConfig
from .config_import import import_config_directory
from .indicator_summary import (
    has_indicator_summary_tables,
    load_indicator_detail_summary,
    load_indicator_summaries,
    load_report_brief,
)
from .measure_summary import has_measure_summary_tables, load_measure_detail_summary
from .report_summary import has_report_summary_tables, load_report_summaries
from .runtime import handle_cli_error, resolve_engine
from .schema import bootstrap_query_schema
from .ui import (
    ImportProgressDisplay,
    render_command_header,
    render_empty_state,
    render_indicator_detail_summary,
    render_indicator_summaries,
    render_indicator_summary_header,
    render_indicator_summary_overview,
    render_import_results,
    render_import_summary,
    render_measure_detail_summary,
    render_measure_summary_header,
    render_report_summaries,
    render_report_indicator_summary_header,
    render_report_summary_header,
    render_report_summary_overview,
    render_schema_bootstrap_header,
    render_schema_bootstrap_result,
)


app = typer.Typer(
    help="CLI utilities for cohort configuration import and inspection.",
    rich_markup_mode="rich",
)


@app.callback()
def app_callback(
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Increase log verbosity (-v INFO, -vv DEBUG). Must come before the subcommand name.",
        ),
    ] = 0,
) -> None:
    """Root CLI app."""
    try:
        OaCohortsConfig.configure_logging(load_stack_config(), verbosity=verbose)
    except (FileNotFoundError, ValueError):
        if verbose:
            OaCohortsConfig.configure_logging(verbosity=verbose)


def _resolve_engine_for_command(console: Console, *, database_url: str | None) -> tuple[sa.Engine, str | None]:
    try:
        return resolve_engine(database_url=database_url)
    except Exception as exc:
        handle_cli_error(console, exc)


@app.command("import-config")
def import_config_command(
    config_path: Path = typer.Argument(..., help="Directory containing the config CSV files."),
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this import."),
    no_dedupe: bool = typer.Option(False, "--no-dedupe", help="Disable duplicate-row cleanup before import."),
    no_create_tables: bool = typer.Option(False, "--no-create-tables", help="Skip Base.metadata.create_all() before importing."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan the import and report changes without writing to the database."),
) -> None:
    console = Console()
    engine, resolved_url = _resolve_engine_for_command(console, database_url=database_url)
    dedupe = not no_dedupe
    create_tables = not no_create_tables

    console.print(
        render_command_header(
            command_name="import-config",
            database_url=resolved_url,
            config_path=str(config_path),
            dedupe=dedupe,
            create_tables=create_tables,
            dry_run=dry_run,
        )
    )

    try:
        with so.sessionmaker(bind=engine, future=True)() as session:
            with ImportProgressDisplay(console, enabled=not dry_run) as progress:
                results = import_config_directory(
                    config_path,
                    session,
                    dedupe=dedupe,
                    create_tables=create_tables,
                    dry_run=dry_run,
                    progress_callback=progress.update if not dry_run else None,
                )
    except Exception as exc:
        handle_cli_error(console, exc)
        return

    console.print(render_import_results(results))
    console.print(render_import_summary(results, dry_run=dry_run))


@app.command("report-summary")
def report_summary_command(
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this query."),
    report_id: int | None = typer.Option(None, "--report-id", help="Limit the summary to a single report ID."),
    short_name: str | None = typer.Option(None, "--short-name", help="Limit the summary to a report short name."),
) -> None:
    console = Console()
    engine, resolved_url = _resolve_engine_for_command(console, database_url=database_url)

    console.print(
        render_report_summary_header(
            database_url=resolved_url,
            report_id=report_id,
            short_name=short_name,
        )
    )

    try:
        with so.sessionmaker(bind=engine, future=True)() as session:
            has_report_tables = has_report_summary_tables(session)
            summaries = load_report_summaries(
                session,
                report_id=report_id,
                short_name=short_name,
            )
    except Exception as exc:
        handle_cli_error(console, exc)
        return

    if not summaries:
        message = "No matching reports were found."
        if not has_report_tables:
            message = (
                "The report table is not available in this database yet. "
                "Run `oa-cohorts import-config ...` first if you have not loaded config."
            )
        console.print(
            render_empty_state(
                message,
                title="Report Summary",
            )
        )
        return

    console.print(render_report_summaries(summaries))
    console.print(render_report_summary_overview(summaries))


@app.command("indicator-summary")
def indicator_summary_command(
    indicator_id: int = typer.Argument(..., help="Indicator ID to summarize."),
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this query."),
) -> None:
    console = Console()
    engine, resolved_url = _resolve_engine_for_command(console, database_url=database_url)

    console.print(
        render_indicator_summary_header(
            database_url=resolved_url,
            indicator_id=indicator_id,
        )
    )

    try:
        with so.sessionmaker(bind=engine, future=True)() as session:
            has_indicator_tables = has_indicator_summary_tables(session)
            summary = load_indicator_detail_summary(session, indicator_id=indicator_id)
    except Exception as exc:
        handle_cli_error(console, exc)
        return

    if not has_indicator_tables:
        console.print(
            render_empty_state(
                "The report and indicator tables are not available in this database yet. "
                "Run `oa-cohorts bootstrap-schema` and then `oa-cohorts import-config ...` first.",
                title="Indicator Summary",
            )
        )
        return

    if summary is None:
        console.print(
            render_empty_state(
                f"No indicator was found for indicator_id={indicator_id}.",
                title="Indicator Summary",
            )
        )
        return

    console.print(render_indicator_detail_summary(summary))


@app.command("report-indicator-summary")
def report_indicator_summary_command(
    report_id: int = typer.Argument(..., help="Report ID to summarize indicators for."),
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this query."),
) -> None:
    console = Console()
    engine, resolved_url = _resolve_engine_for_command(console, database_url=database_url)

    console.print(
        render_report_indicator_summary_header(
            database_url=resolved_url,
            report_id=report_id,
        )
    )

    try:
        with so.sessionmaker(bind=engine, future=True)() as session:
            has_indicator_tables = has_indicator_summary_tables(session)
            report_brief = load_report_brief(session, report_id=report_id)
            summaries = load_indicator_summaries(session, report_id=report_id)
    except Exception as exc:
        handle_cli_error(console, exc)
        return

    if not has_indicator_tables:
        console.print(
            render_empty_state(
                "The report and indicator tables are not available in this database yet. "
                "Run `oa-cohorts bootstrap-schema` and then `oa-cohorts import-config ...` first.",
                title="Indicator Summary",
            )
        )
        return

    if report_brief is None:
        console.print(
            render_empty_state(
                f"No report was found for report_id={report_id}.",
                title="Indicator Summary",
            )
        )
        return

    report_name, report_short_name = report_brief
    if not summaries:
        console.print(
            render_empty_state(
                f"Report {report_name} ({report_short_name}) has no linked indicators.",
                title="Indicator Summary",
            )
        )
        return

    console.print(
        render_indicator_summaries(
            summaries,
            report_name=report_name,
            report_short_name=report_short_name,
        )
    )
    console.print(
        render_indicator_summary_overview(
            summaries,
            report_name=report_name,
            report_short_name=report_short_name,
        )
    )


@app.command("measure-summary")
def measure_summary_command(
    measure_id: int = typer.Argument(..., help="Measure ID to summarize."),
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this query."),
) -> None:
    console = Console()
    engine, resolved_url = _resolve_engine_for_command(console, database_url=database_url)

    console.print(
        render_measure_summary_header(
            database_url=resolved_url,
            measure_id=measure_id,
        )
    )

    try:
        with so.sessionmaker(bind=engine, future=True)() as session:
            has_measure_tables = has_measure_summary_tables(session)
            summary = load_measure_detail_summary(session, measure_id=measure_id)
    except Exception as exc:
        handle_cli_error(console, exc)
        return

    if not has_measure_tables:
        console.print(
            render_empty_state(
                "The measure table is not available in this database yet. "
                "Run `oa-cohorts bootstrap-schema` and then `oa-cohorts import-config ...` first.",
                title="Measure Summary",
            )
        )
        return

    if summary is None:
        console.print(
            render_empty_state(
                f"No measure was found for measure_id={measure_id}.",
                title="Measure Summary",
            )
        )
        return

    console.print(render_measure_detail_summary(summary))


@app.command("bootstrap-schema")
def bootstrap_schema_command(
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this bootstrap."),
) -> None:
    console = Console()
    engine, resolved_url = _resolve_engine_for_command(console, database_url=database_url)

    console.print(render_schema_bootstrap_header(database_url=resolved_url))

    try:
        result = bootstrap_query_schema(engine)
    except Exception as exc:
        handle_cli_error(console, exc)
        return

    console.print(render_schema_bootstrap_result(result))


def main(argv: Sequence[str] | None = None) -> int:
    command = get_command(app)
    try:
        command.main(
            args=list(argv) if argv is not None else None,
            prog_name="oa-cohorts",
            standalone_mode=True,
        )
        return 0
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 1


__all__ = ["app", "main"]
