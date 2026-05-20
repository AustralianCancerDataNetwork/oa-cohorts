from __future__ import annotations

from rich import box
from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from .config_import import ImportProgressEvent, TableImportResult
from .indicator_summary import IndicatorDetailSummary, IndicatorSummary, MeasureSummary
from .measure_summary import MeasureDetailSummary
from .report_summary import ReportSummary
from .schema import SchemaBootstrapResult


def _render_header_panel(rows: list[tuple[str, RenderableType]]) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan")
    grid.add_column()
    for label, value in rows:
        grid.add_row(label, value)
    return Panel.fit(grid, title="[bold]oa-cohorts[/bold]", border_style="blue")


def render_command_header(
    *,
    command_name: str,
    database_url: str | None,
    config_path: str,
    dedupe: bool,
    create_tables: bool,
    dry_run: bool,
) -> Panel:
    return _render_header_panel(
        [
            ("Command", command_name),
            ("Config path", config_path),
            ("Database", database_url or "ENGINE / default registry engine"),
            ("Dedupe", "yes" if dedupe else "no"),
            ("Create tables", "yes" if create_tables else "no"),
            ("Mode", Text("dry-run" if dry_run else "apply", style="cyan" if dry_run else "green")),
        ]
    )


def render_report_summary_header(
    *,
    database_url: str | None,
    report_id: int | None,
    short_name: str | None,
) -> Panel:
    return _render_header_panel(
        [
            ("Command", "report-summary"),
            ("Database", database_url or "ENGINE / default registry engine"),
            ("Report ID", str(report_id) if report_id is not None else "all"),
            ("Short name", short_name or "all"),
        ]
    )


def render_indicator_summary_header(
    *,
    database_url: str | None,
    indicator_id: int,
) -> Panel:
    return _render_header_panel(
        [
            ("Command", "indicator-summary"),
            ("Database", database_url or "ENGINE / default registry engine"),
            ("Indicator ID", str(indicator_id)),
        ]
    )


def render_report_indicator_summary_header(
    *,
    database_url: str | None,
    report_id: int,
) -> Panel:
    return _render_header_panel(
        [
            ("Command", "report-indicator-summary"),
            ("Database", database_url or "ENGINE / default registry engine"),
            ("Report ID", str(report_id)),
        ]
    )


def render_measure_summary_header(
    *,
    database_url: str | None,
    measure_id: int,
) -> Panel:
    return _render_header_panel(
        [
            ("Command", "measure-summary"),
            ("Database", database_url or "ENGINE / default registry engine"),
            ("Measure ID", str(measure_id)),
        ]
    )


def render_error(message: str, *, title: str = "Import failed") -> Panel:
    return Panel.fit(
        Text(message, style="bold red"),
        title=f"[bold red]{title}[/bold red]",
        border_style="red",
    )


def render_empty_state(message: str, *, title: str) -> Panel:
    return Panel.fit(
        Text(message, style="yellow"),
        title=f"[bold]{title}[/bold]",
        border_style="yellow",
    )


def render_import_results(results: list[TableImportResult]) -> Table:
    table = Table(title="Config Import Results", box=box.SIMPLE_HEAVY, header_style="bold")
    table.add_column("Table", style="cyan")
    table.add_column("File")
    table.add_column("Rows", justify="right")
    table.add_column("Dropped Dups", justify="right")
    table.add_column("Skipped Existing", justify="right")
    table.add_column("Replaced", justify="right")
    table.add_column("Inserted", justify="right")

    for result in results:
        table.add_row(
            result.table_name,
            result.file_name,
            str(result.total_rows),
            str(result.dropped_duplicate_rows),
            str(result.skipped_existing_rows),
            str(result.replaced_rows),
            str(result.inserted_rows),
        )

    return table


def render_import_summary(results: list[TableImportResult], *, dry_run: bool) -> Panel:
    total_rows = sum(result.total_rows for result in results)
    total_dups = sum(result.dropped_duplicate_rows for result in results)
    total_skipped = sum(result.skipped_existing_rows for result in results)
    total_replaced = sum(result.replaced_rows for result in results)
    total_inserted = sum(result.inserted_rows for result in results)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan")
    grid.add_column()
    grid.add_row("Tables", str(len(results)))
    grid.add_row("Rows read", str(total_rows))
    grid.add_row("Dropped duplicates", str(total_dups))
    grid.add_row("Skipped existing", str(total_skipped))
    grid.add_row("Rows replaced", str(total_replaced))
    grid.add_row("Rows inserted", str(total_inserted))
    grid.add_row(
        "Result",
        "Import planned only; no database writes were made." if dry_run else "Import applied successfully.",
    )
    return Panel.fit(
        grid,
        title="[bold]Summary[/bold]",
        border_style="cyan" if dry_run else "green",
    )


def render_report_summaries(summaries: list[ReportSummary]) -> Panel | Table:
    if len(summaries) == 1:
        summary = summaries[0]
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan")
        grid.add_column()
        grid.add_row("ID", str(summary.report_id))
        grid.add_row("Name", summary.report_name)
        grid.add_row("Short name", summary.report_short_name)
        grid.add_row("Author", summary.author)
        grid.add_row("Owner", summary.owner or "-")
        grid.add_row("Cohorts", ", ".join(summary.cohort_names) or "-")
        grid.add_row("Primary cohorts", ", ".join(summary.primary_cohort_names) or "-")
        grid.add_row("Indicator count", str(summary.indicator_count))
        grid.add_row("Description", summary.description or "-")
        return Panel.fit(grid, title="[bold]Report Summary[/bold]", border_style="green")

    table = Table(title="Report Summaries", box=box.SIMPLE_HEAVY, header_style="bold")
    table.add_column("ID", justify="right", style="cyan")
    table.add_column("Short Name", style="cyan")
    table.add_column("Name")
    table.add_column("Cohorts", justify="right")
    table.add_column("Primary Cohorts")
    table.add_column("Indicators", justify="right")
    table.add_column("Author")

    for summary in summaries:
        table.add_row(
            str(summary.report_id),
            summary.report_short_name,
            summary.report_name,
            str(summary.cohort_count),
            ", ".join(summary.primary_cohort_names) or "-",
            str(summary.indicator_count),
            summary.author,
        )

    return table


def render_report_summary_overview(summaries: list[ReportSummary]) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan")
    grid.add_column()
    grid.add_row("Reports", str(len(summaries)))
    grid.add_row(
        "Indicators",
        str(sum(summary.indicator_count for summary in summaries)),
    )
    grid.add_row(
        "Primary cohorts",
        str(sum(len(summary.primary_cohort_names) for summary in summaries)),
    )
    return Panel.fit(grid, title="[bold]Summary[/bold]", border_style="green")


def render_schema_bootstrap_header(*, database_url: str | None) -> Panel:
    return _render_header_panel(
        [
            ("Command", "bootstrap-schema"),
            ("Database", database_url or "ENGINE / default registry engine"),
            ("Scope", "oa-cohorts query/config schema"),
        ]
    )


def render_schema_bootstrap_result(result: SchemaBootstrapResult) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan")
    grid.add_column()
    grid.add_row("Tables in scope", str(result.table_count))
    grid.add_row("Created now", str(len(result.created_tables)))
    grid.add_row("Already present", str(len(result.existing_tables)))
    grid.add_row("Created tables", ", ".join(result.created_tables) or "-")
    return Panel.fit(grid, title="[bold]Schema Bootstrap[/bold]", border_style="green")


def render_indicator_summaries(
    summaries: list[IndicatorSummary],
    *,
    report_name: str,
    report_short_name: str,
) -> RenderableType:
    if len(summaries) == 1:
        summary = summaries[0]
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan")
        grid.add_column()
        grid.add_row("ID", str(summary.indicator_id))
        grid.add_row("Description", summary.description)
        grid.add_row("Reference", summary.reference or "-")
        grid.add_row(
            "Numerator",
            f"{summary.numerator_measure_id}: {summary.numerator_measure_name}",
        )
        grid.add_row(
            "Denominator",
            f"{summary.denominator_measure_id}: {summary.denominator_measure_name}",
        )
        grid.add_row("Benchmark", summary.benchmark_summary)
        return Panel.fit(
            grid,
            title=f"[bold]Indicator Summary: {report_name} ({report_short_name})[/bold]",
            border_style="green",
        )

    table = Table(
        title=f"Indicator Summary: {report_name} ({report_short_name})",
        box=box.SIMPLE_HEAVY,
        header_style="bold",
    )
    table.add_column("ID", justify="right", style="cyan")
    table.add_column("Description")
    table.add_column("Reference")
    table.add_column("Numerator")
    table.add_column("Denominator")
    table.add_column("Benchmark")

    for summary in summaries:
        table.add_row(
            str(summary.indicator_id),
            summary.description,
            summary.reference or "-",
            f"{summary.numerator_measure_id}: {summary.numerator_measure_name}",
            f"{summary.denominator_measure_id}: {summary.denominator_measure_name}",
            summary.benchmark_summary,
        )

    return table


def render_indicator_detail_summary(summary: IndicatorDetailSummary) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan")
    grid.add_column()
    grid.add_row("ID", str(summary.indicator_id))
    grid.add_row("Description", summary.description)
    grid.add_row("Reference", summary.reference or "-")
    grid.add_row("Reports", ", ".join(summary.report_memberships) or "-")
    grid.add_row("Benchmark", summary.benchmark_summary)
    grid.add_row("Numerator", _render_measure_summary(summary.numerator_measure))
    grid.add_row("Denominator", _render_measure_summary(summary.denominator_measure))
    return Panel.fit(grid, title="[bold]Indicator Summary[/bold]", border_style="green")


def render_measure_detail_summary(summary: MeasureDetailSummary) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan")
    grid.add_column()
    grid.add_row("ID", str(summary.measure_id))
    grid.add_row("Name", summary.name)
    grid.add_row("Kind", summary.summary_kind)
    grid.add_row("Combination", summary.combination)
    grid.add_row("Episode override", "yes" if summary.person_ep_override else "no")
    grid.add_row("Subquery", _format_subquery_summary(summary))
    grid.add_row("Parents", ", ".join(summary.parent_measure_names) or "-")
    grid.add_row("Children", ", ".join(summary.child_measure_names) or "-")
    grid.add_row("Numerator in", _format_usage_lines(summary.numerator_indicator_usages))
    grid.add_row("Denominator in", _format_usage_lines(summary.denominator_indicator_usages))
    grid.add_row("Cohort defs", _format_usage_lines(summary.cohort_definition_usages))
    return Panel.fit(grid, title="[bold]Measure Summary[/bold]", border_style="green")


def render_indicator_summary_overview(
    summaries: list[IndicatorSummary],
    *,
    report_name: str,
    report_short_name: str,
) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan")
    grid.add_column()
    grid.add_row("Report", f"{report_name} ({report_short_name})")
    grid.add_row("Indicators", str(len(summaries)))
    grid.add_row(
        "Measures referenced",
        str(
            len(
                {
                    summary.numerator_measure_id
                    for summary in summaries
                }
                | {
                    summary.denominator_measure_id
                    for summary in summaries
                }
            )
        ),
    )
    return Panel.fit(grid, title="[bold]Summary[/bold]", border_style="green")


def _render_measure_summary(summary: MeasureSummary) -> Text:
    lines = [
        f"{summary.measure_id}: {summary.name}",
        f"Combination: {summary.combination}",
        f"Episode override: {'yes' if summary.person_ep_override else 'no'}",
    ]
    if summary.subquery_name is not None:
        subquery_detail = summary.subquery_name
        if summary.subquery_short_name:
            subquery_detail = f"{subquery_detail} ({summary.subquery_short_name})"
        lines.append(f"Subquery: {subquery_detail}")
    if summary.subquery_target is not None:
        lines.append(f"Target: {summary.subquery_target}")
    if summary.subquery_temporality is not None:
        lines.append(f"Temporality: {summary.subquery_temporality}")
    if summary.child_measure_names:
        lines.append(f"Children: {', '.join(summary.child_measure_names)}")
    return Text("\n".join(lines))


def _format_subquery_summary(summary: MeasureDetailSummary) -> Text:
    if summary.subquery_name is None:
        return Text("-")

    lines = [summary.subquery_name]
    if summary.subquery_short_name:
        lines.append(f"Short name: {summary.subquery_short_name}")
    if summary.subquery_target:
        lines.append(f"Target: {summary.subquery_target}")
    if summary.subquery_temporality:
        lines.append(f"Temporality: {summary.subquery_temporality}")
    return Text("\n".join(lines))


def _format_usage_lines(values: tuple[str, ...]) -> Text:
    return Text("\n".join(values) if values else "-")


class ImportProgressDisplay:
    def __init__(self, console: Console, *, enabled: bool = True) -> None:
        self.console = console
        self.enabled = enabled
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def __enter__(self) -> "ImportProgressDisplay":
        if self.enabled:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(bar_width=None),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=self.console,
                transient=False,
            )
            self._progress.__enter__()
            self._task_id = self._progress.add_task(
                "Preparing config import...",
                total=1.0,
                completed=0,
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._progress is not None:
            self._progress.__exit__(exc_type, exc, tb)

    def update(self, event: ImportProgressEvent) -> None:
        if self._progress is None or self._task_id is None:
            return

        completed = event.table_index + (0.5 if event.phase not in {"start", "complete"} else 0.0)
        total = max(float(event.table_count or 1), 1.0)
        description = event.detail
        self._progress.update(
            self._task_id,
            total=total,
            completed=min(completed, total),
            description=description,
        )

        if event.phase == "complete" and event.table_name:
            status = "planned" if event.dry_run else "processed"
            self._progress.console.print(
                f"[green]{status}[/green] [bold]{event.table_name}[/bold] ({event.table_index}/{event.table_count})"
            )
