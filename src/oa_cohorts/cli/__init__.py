from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import sqlalchemy as sa
import sqlalchemy.orm as so
import typer
from oa_configurator import load_stack_config
from rich.console import Console
from typer.main import get_command

from ..config import OaCohortsConfig
from ..schema import check_schema, history, migration_status, upgrade, upgrade_sql
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
from .schema import GuardMode, bootstrap_query_schema, guard_schema
from .ui import (
    ImportProgressDisplay,
    render_command_header,
    render_empty_state,
    render_error,
    render_import_results,
    render_import_summary,
    render_indicator_detail_summary,
    render_indicator_summaries,
    render_indicator_summary_header,
    render_indicator_summary_overview,
    render_measure_detail_summary,
    render_measure_summary_header,
    render_migration_history,
    render_report_indicator_summary_header,
    render_report_summaries,
    render_report_summary_header,
    render_report_summary_overview,
    render_schema_bootstrap_header,
    render_schema_bootstrap_result,
    render_schema_check_result,
    render_schema_command_header,
    render_schema_status,
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


def _resolve_engine_for_command(
    console: Console,
    *,
    database_url: str | None,
    guard: GuardMode = GuardMode.read,
    ignore_schema_drift: bool = False,
    auto_bootstrap: bool = False,
) -> tuple[sa.Engine, str | None]:
    """Resolve the engine and, unless the command opts out, verify the schema.

    This is the single chokepoint every database command already passes
    through, which makes it the place a drift warning reaches the whole
    workflow without each command having to remember to ask.
    """
    try:
        engine, resolved_url = resolve_engine(database_url=database_url)
    except Exception as exc:
        handle_cli_error(console, exc)

    guard_schema(
        console,
        engine,
        mode=guard,
        ignore_drift=ignore_schema_drift,
        auto_bootstrap=auto_bootstrap,
    )
    return engine, resolved_url


@app.command("import-config")
def import_config_command(
    config_path: Path = typer.Argument(..., help="Directory containing the config CSV files."),
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this import."),
    no_dedupe: bool = typer.Option(False, "--no-dedupe", help="Disable duplicate-row cleanup before import."),
    no_create_tables: bool = typer.Option(False, "--no-create-tables", help="Skip Base.metadata.create_all() before importing."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan the import and report changes without writing to the database."),
    ignore_schema_drift: bool = typer.Option(
        False,
        "--ignore-schema-drift",
        help="Import even if the database schema does not match the models.",
    ),
) -> None:
    console = Console()
    dedupe = not no_dedupe
    create_tables = not no_create_tables

    # A dry run writes nothing, so drift is a warning rather than a blocker.
    # --create-tables already meant "build the schema if it is not there", so
    # an empty database is initialised through the migrations rather than
    # blocked; anything already present is never migrated implicitly.
    engine, resolved_url = _resolve_engine_for_command(
        console,
        database_url=database_url,
        guard=GuardMode.read if dry_run else GuardMode.write,
        ignore_schema_drift=ignore_schema_drift,
        auto_bootstrap=create_tables and not dry_run,
    )

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
        with (
            so.sessionmaker(bind=engine, future=True)() as session,
            ImportProgressDisplay(console, enabled=not dry_run) as progress,
        ):
            results = import_config_directory(
                config_path,
                session,
                dedupe=dedupe,
                create_tables=create_tables,
                dry_run=dry_run,
                progress_callback=progress.update if not dry_run else None,
            )
    except Exception as exc:
        handle_cli_error(console, exc, engine=engine)
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
        handle_cli_error(console, exc, engine=engine)
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
        handle_cli_error(console, exc, engine=engine)
        return

    if not has_indicator_tables:
        console.print(
            render_empty_state(
                "The report and indicator tables are not available in this database yet. "
                "Run `oa-cohorts schema bootstrap` and then `oa-cohorts import-config ...` first.",
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
        handle_cli_error(console, exc, engine=engine)
        return

    if not has_indicator_tables:
        console.print(
            render_empty_state(
                "The report and indicator tables are not available in this database yet. "
                "Run `oa-cohorts schema bootstrap` and then `oa-cohorts import-config ...` first.",
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
        handle_cli_error(console, exc, engine=engine)
        return

    if not has_measure_tables:
        console.print(
            render_empty_state(
                "The measure table is not available in this database yet. "
                "Run `oa-cohorts schema bootstrap` and then `oa-cohorts import-config ...` first.",
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


schema_app = typer.Typer(
    help=(
        "Inspect and migrate the dashboard schema. Migrations are never applied "
        "automatically; 'schema upgrade' is the only command that changes DDL."
    ),
    rich_markup_mode="rich",
)
app.add_typer(schema_app, name="schema")


def _schema_engine(console: Console, database_url: str | None) -> tuple[sa.Engine, str | None]:
    """Resolve an engine without guarding — these commands are the remedy."""
    return _resolve_engine_for_command(console, database_url=database_url, guard=GuardMode.off)


@schema_app.command("check")
def schema_check_command(
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this check."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print nothing; signal drift through the exit code only."),
) -> None:
    """Compare the live schema to the models and report every difference.

    Runs Alembic's own autogenerate comparison. Exits 1 when the schema does
    not match. Note that enum value sets are not compared — see
    docs/schema_management.md.
    """
    console = Console()
    engine, resolved_url = _schema_engine(console, database_url)

    if not quiet:
        console.print(render_schema_command_header(command_name="schema check", database_url=resolved_url))

    try:
        result = check_schema(engine)
    except Exception as exc:
        handle_cli_error(console, exc, engine=engine)
        return

    if not quiet:
        console.print(render_schema_check_result(result))

    if not result.is_clean:
        raise typer.Exit(code=1)


@schema_app.command("status")
def schema_status_command(
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this query."),
) -> None:
    """Show the current revision, the head, and what is pending."""
    console = Console()
    engine, resolved_url = _schema_engine(console, database_url)

    try:
        status = migration_status(engine)
    except Exception as exc:
        handle_cli_error(console, exc, engine=engine)
        return

    console.print(render_schema_status(status, database_url=resolved_url))


@schema_app.command("bootstrap")
def schema_bootstrap_command(
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this bootstrap."),
    adopt_on_drift: bool = typer.Option(
        False,
        "--adopt-on-drift",
        help="Adopt an existing schema as the baseline even if it does not match the models.",
    ),
    revision: str | None = typer.Option(
        None,
        "--revision",
        help=(
            "Stamp this revision instead of head, for a schema that matches an earlier "
            "one. A database from 0.8.7 or earlier needs '--revision 0001_baseline', "
            "then 'schema upgrade'."
        ),
    ),
) -> None:
    """Bring a database under migration management.

    Creates the tables if the database is empty; otherwise checks the existing
    schema and adopts it without running any DDL. Pass --revision when the
    schema matches an earlier revision than the current models.
    """
    console = Console()
    engine, resolved_url = _schema_engine(console, database_url)

    console.print(render_schema_bootstrap_header(database_url=resolved_url))

    try:
        result = bootstrap_query_schema(engine, adopt_on_drift=adopt_on_drift, revision=revision)
    except Exception as exc:
        handle_cli_error(console, exc, engine=engine)
        return

    console.print(render_schema_bootstrap_result(result))
    if result.check is not None and not result.check.is_clean:
        console.print(render_schema_check_result(result.check))
    if result.blocked:
        raise typer.Exit(code=1)


@schema_app.command("upgrade")
def schema_upgrade_command(
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this upgrade."),
    revision: str = typer.Option("head", "--revision", help="Target revision. Defaults to head."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Apply pending migrations. The only command that changes the schema."""
    console = Console()
    engine, resolved_url = _schema_engine(console, database_url)

    try:
        status = migration_status(engine)
    except Exception as exc:
        handle_cli_error(console, exc, engine=engine)
        return

    console.print(render_schema_status(status, database_url=resolved_url))

    if not status.is_stamped:
        console.print(
            render_error(
                "This database is not under migration management yet. "
                "Run 'oa-cohorts schema bootstrap' first.",
                title="Upgrade refused",
            )
        )
        raise typer.Exit(code=1)

    if status.is_up_to_date:
        console.print(render_empty_state("Already at head; nothing to apply.", title="Schema Upgrade"))
        return

    target = resolved_url or "the configured database"
    if not yes and not typer.confirm(f"Apply {len(status.pending)} migration(s) to {target}?"):
        console.print(render_empty_state("Upgrade cancelled.", title="Schema Upgrade"))
        raise typer.Exit(code=1)

    try:
        upgrade(engine, revision)
        applied = migration_status(engine)
    except Exception as exc:
        handle_cli_error(console, exc, engine=engine)
        return

    console.print(render_schema_status(applied, database_url=resolved_url))


@schema_app.command("sql")
def schema_sql_command(
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this render."),
    revision: str = typer.Option("head", "--revision", help="Target revision. Defaults to head."),
) -> None:
    """Print the SQL an upgrade would run, without executing anything.

    For review, or for handing to whoever owns the database.
    """
    console = Console()
    engine, _ = _schema_engine(console, database_url)

    try:
        sql = upgrade_sql(engine, revision)
    except Exception as exc:
        handle_cli_error(console, exc, engine=engine)
        return

    # Plain print: this output is meant to be piped to a file or a DBA.
    print(sql)


@schema_app.command("history")
def schema_history_command() -> None:
    """List every migration, oldest first."""
    console = Console()
    console.print(render_migration_history(history()))


@app.command("bootstrap-schema", hidden=True)
def bootstrap_schema_command(
    database_url: str | None = typer.Option(None, help="Override the runtime database URL for this bootstrap."),
) -> None:
    """Deprecated alias for 'oa-cohorts schema bootstrap'."""
    console = Console()
    console.print(
        render_empty_state(
            "'bootstrap-schema' is deprecated; use 'oa-cohorts schema bootstrap'.",
            title="Deprecated command",
        )
    )
    schema_bootstrap_command(database_url=database_url, adopt_on_drift=False, revision=None)

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
