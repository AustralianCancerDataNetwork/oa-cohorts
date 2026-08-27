from __future__ import annotations

import csv
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import sqlalchemy.orm as so
from orm_loader.helpers import Base

from ..core.coercion import coerce_column_value, parse_bool
from ..query import (
    DashCohort,
    DashCohortDef,
    Indicator,
    Measure,
    MeasureRelationship,
    MeasureTemporalWindow,
    Phenotype,
    PhenotypeDefinition,
    QueryRule,
    Report,
    ReportCohortMap,
    ReportIndicatorMap,
    Subquery,
    dash_cohort_def_map,
    subquery_rule_map,
)

RowTransform = Callable[[Any], Any]
ImportProgressCallback = Callable[["ImportProgressEvent"], None]


def _model_table(model: type[Base]) -> sa.Table:
    """Narrow ``Model.__table__``, typed ``FromClause``, to the ``Table`` it always is here."""
    table = model.__table__
    if not isinstance(table, sa.Table):
        raise TypeError(f"{model.__name__}.__table__ is {type(table).__name__}, not a Table")
    return table


def _bool_default_true(v: Any) -> bool:
    result = parse_bool(v)
    return True if result is None else result


@dataclass(frozen=True)
class TableImportSpec:
    table: sa.Table
    filenames: tuple[str, ...]
    value_transforms: dict[str, RowTransform] = field(default_factory=dict)
    optional: bool = False

    @property
    def label(self) -> str:
        return self.table.name

    def resolve_path(self, config_path: Path) -> Path:
        for filename in self.filenames:
            candidate = config_path / filename
            if candidate.exists():
                return candidate
        expected = ", ".join(self.filenames)
        raise FileNotFoundError(f"Missing CSV for {self.table.name}: expected one of {expected}")


@dataclass(frozen=True)
class TableImportResult:
    table_name: str
    file_name: str
    total_rows: int
    dropped_duplicate_rows: int
    skipped_existing_rows: int
    replaced_rows: int
    inserted_rows: int


@dataclass(frozen=True)
class ImportProgressEvent:
    phase: str
    table_name: str | None
    table_index: int
    table_count: int
    detail: str
    dry_run: bool


def _emit_progress(
    progress_callback: ImportProgressCallback | None,
    *,
    phase: str,
    table_name: str | None,
    table_index: int,
    table_count: int,
    detail: str,
    dry_run: bool,
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        ImportProgressEvent(
            phase=phase,
            table_name=table_name,
            table_index=table_index,
            table_count=table_count,
            detail=detail,
            dry_run=dry_run,
        )
    )


def _coerce_value(column: sa.Column[Any], value: Any) -> Any:
    return coerce_column_value(column, value)


def _normalise_header(row: dict[str, str]) -> dict[str, str]:
    normalised: dict[str, str] = {}
    for key, value in row.items():
        clean_key = key.strip()
        if clean_key in normalised and normalised[clean_key] != value:
            raise ValueError(
                f"CSV contains conflicting values for column {clean_key!r}"
            )
        normalised[clean_key] = value
    return normalised


def _required_columns(table: sa.Table) -> set[str]:
    return {
        column.name
        for column in table.columns
        if not column.nullable and not column.default and not column.server_default
    }


def _clean_row(raw_row: dict[str, str], spec: TableImportSpec) -> dict[str, Any]:
    row = _normalise_header(raw_row)
    available_columns = {column.name: column for column in spec.table.columns}
    cleaned: dict[str, Any] = {}

    for key, value in row.items():
        if key not in available_columns:
            continue
        transformed = spec.value_transforms.get(key, lambda item: item)(value)
        cleaned[key] = _coerce_value(available_columns[key], transformed)

    missing = _required_columns(spec.table) - cleaned.keys()
    if missing:
        raise ValueError(
            f"Missing required columns for {spec.table.name}: {sorted(missing)}"
        )

    return cleaned


def _row_signature(row: dict[str, Any], column_names: Iterable[str]) -> tuple[Any, ...]:
    return tuple(row.get(column_name) for column_name in column_names)


def _present_columns(row: dict[str, Any], column_names: Iterable[str]) -> list[str]:
    return [name for name in column_names if name in row]


def _updatable_columns(
    row: dict[str, Any],
    present: Iterable[str],
    pk_names: Iterable[str],
) -> dict[str, Any]:
    """The columns an UPDATE for this row should set: present, and not part of the key.

    Returning only present columns is what makes an older, narrower export a no-op on the
    columns it does not know about rather than a wipe of them. It also keeps the emitted
    statement legal: ``values()`` with nothing in it renders ``UPDATE t SET  WHERE ...``,
    which is a syntax error rather than an empty update.
    """
    pk_set = set(pk_names)
    return {name: row[name] for name in present if name not in pk_set}


def _dedupe_incoming_rows(
    rows: list[dict[str, Any]],
    *,
    table: sa.Table,
) -> tuple[list[dict[str, Any]], int]:
    column_names = [column.name for column in table.columns]
    primary_keys = [column.name for column in table.primary_key.columns]

    deduped: list[dict[str, Any]] = []
    seen_full_rows: set[tuple[Any, ...]] = set()
    seen_pk_rows: dict[tuple[Any, ...], tuple[Any, ...]] = {}
    dropped = 0

    for row in rows:
        full_signature = _row_signature(row, column_names)
        if full_signature in seen_full_rows:
            dropped += 1
            continue
        if primary_keys:
            pk_signature = _row_signature(row, primary_keys)
            previous = seen_pk_rows.get(pk_signature)
            if previous is not None and previous != full_signature:
                raise ValueError(
                    f"Conflicting duplicate primary key {pk_signature!r} found in {table.name}"
                )
            seen_pk_rows[pk_signature] = full_signature

        seen_full_rows.add(full_signature)
        deduped.append(row)

    return deduped, dropped


def _load_rows(path: Path, spec: TableImportSpec) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        return [_clean_row(row, spec) for row in reader]


def _delete_existing_row(
    session: so.Session,
    table: sa.Table,
    pk_names: list[str],
    row: dict[str, Any],
) -> None:
    predicate = sa.and_(*[table.c[name] == row[name] for name in pk_names])
    session.execute(sa.delete(table).where(predicate))


def _table_exists(session: so.Session, table: sa.Table) -> bool:
    bind = session.get_bind()
    inspector = sa.inspect(bind)
    return inspector.has_table(table.name, schema=table.schema)


def _existing_rows(session: so.Session, table: sa.Table) -> list[dict[str, Any]]:
    if not _table_exists(session, table):
        return []
    return [dict(row) for row in session.execute(sa.select(table)).mappings().all()]


def _sync_table(
    session: so.Session,
    spec: TableImportSpec,
    path: Path,
    *,
    table_index: int,
    table_count: int,
    dedupe: bool = True,
    dry_run: bool = False,
    progress_callback: ImportProgressCallback | None = None,
) -> TableImportResult:
    _emit_progress(
        progress_callback,
        phase="load",
        table_name=spec.table.name,
        table_index=table_index,
        table_count=table_count,
        detail=f"Loading {path.name}",
        dry_run=dry_run,
    )
    incoming_rows = _load_rows(path, spec)
    total_rows = len(incoming_rows)

    dropped_duplicate_rows = 0
    if dedupe:
        _emit_progress(
            progress_callback,
            phase="dedupe",
            table_name=spec.table.name,
            table_index=table_index,
            table_count=table_count,
            detail=f"Deduplicating {spec.table.name}",
            dry_run=dry_run,
        )
        incoming_rows, dropped_duplicate_rows = _dedupe_incoming_rows(
            incoming_rows,
            table=spec.table,
        )

    _emit_progress(
        progress_callback,
        phase="compare",
        table_name=spec.table.name,
        table_index=table_index,
        table_count=table_count,
        detail=f"Comparing {spec.table.name} against database",
        dry_run=dry_run,
    )
    existing_rows = _existing_rows(session, spec.table)

    column_names = [column.name for column in spec.table.columns]
    pk_names = [column.name for column in spec.table.primary_key.columns]

    inserted_rows: list[dict[str, Any]] = []
    replaced_rows: list[dict[str, Any]] = []
    skipped_existing_rows = 0

    if pk_names:
        existing_by_pk = {
            _row_signature(row, pk_names): row
            for row in existing_rows
        }
        for row in incoming_rows:
            pk_signature = _row_signature(row, pk_names)
            existing = existing_by_pk.get(pk_signature)
            if existing is None:
                inserted_rows.append(row)
                continue
            compared = _present_columns(row, column_names)
            if _row_signature(existing, compared) == _row_signature(row, compared):
                skipped_existing_rows += 1
                continue
            if not _updatable_columns(row, compared, pk_names):
                # Nothing to write: every column the file carried is part of the key.
                skipped_existing_rows += 1
                continue
            replaced_rows.append(row)
    else:
        existing_signatures = {
            _row_signature(row, column_names)
            for row in existing_rows
        }
        for row in incoming_rows:
            full_signature = _row_signature(row, column_names)
            if full_signature in existing_signatures:
                skipped_existing_rows += 1
                continue
            inserted_rows.append(row)
            existing_signatures.add(full_signature)

    if not dry_run:
        _emit_progress(
            progress_callback,
            phase="write",
            table_name=spec.table.name,
            table_index=table_index,
            table_count=table_count,
            detail=f"Writing {spec.table.name}",
            dry_run=dry_run,
        )
        for row in replaced_rows:
            predicate = sa.and_(*[spec.table.c[name] == row[name] for name in pk_names])
            updates = _updatable_columns(row, _present_columns(row, column_names), pk_names)
            session.execute(sa.update(spec.table).where(predicate).values(**updates))

        if inserted_rows:
            session.execute(sa.insert(spec.table), inserted_rows)
        session.commit()

    _emit_progress(
        progress_callback,
        phase="complete",
        table_name=spec.table.name,
        table_index=table_index,
        table_count=table_count,
        detail=f"{'Planned' if dry_run else 'Imported'} {spec.table.name}",
        dry_run=dry_run,
    )

    return TableImportResult(
        table_name=spec.table.name,
        file_name=path.name,
        total_rows=total_rows,
        dropped_duplicate_rows=dropped_duplicate_rows,
        skipped_existing_rows=skipped_existing_rows,
        replaced_rows=len(replaced_rows),
        inserted_rows=len(inserted_rows),
    )


CONFIG_IMPORT_SPECS: tuple[TableImportSpec, ...] = (
    TableImportSpec(
        table=_model_table(Phenotype),
        filenames=("phenotype.csv",),
    ),
    TableImportSpec(
        table=_model_table(PhenotypeDefinition),
        filenames=("phenotype_definition.csv",),
    ),
    TableImportSpec(
        table=_model_table(QueryRule),
        filenames=("query_rule.csv",),
    ),
    TableImportSpec(
        table=_model_table(Subquery),
        filenames=("subquery.csv",),
    ),
    TableImportSpec(
        table=subquery_rule_map,
        filenames=("subquery_rule_map.csv",),
    ),
    TableImportSpec(
        table=_model_table(Measure),
        filenames=("measure.csv",),
        value_transforms={
            "person_ep_override": parse_bool,
        },
    ),
    TableImportSpec(
        table=_model_table(MeasureRelationship),
        filenames=("measure_relationship.csv",),
    ),
    TableImportSpec(
        table=_model_table(MeasureTemporalWindow),
        filenames=("measure_temporal_window.csv",),
        value_transforms={"require_same_resolver": _bool_default_true},
        optional=True,
    ),
    TableImportSpec(
        table=_model_table(DashCohortDef),
        filenames=("dash_cohort_def.csv",),
    ),
    TableImportSpec(
        table=_model_table(DashCohort),
        filenames=("dash_cohort.csv",),
    ),
    TableImportSpec(
        table=dash_cohort_def_map,
        filenames=("dash_cohort_def_map.csv",),
    ),
    TableImportSpec(
        table=_model_table(Indicator),
        filenames=("indicator.csv",),
    ),
    TableImportSpec(
        table=_model_table(Report),
        filenames=("report.csv",),
    ),
    TableImportSpec(
        table=_model_table(ReportCohortMap),
        filenames=("report_cohort_map.csv",),
        value_transforms={
            "primary_cohort": parse_bool,
        },
    ),
    TableImportSpec(
        table=_model_table(ReportIndicatorMap),
        filenames=("report_indicator_map.csv",),
    ),
)


def import_config_directory(
    config_path: Path,
    session: so.Session,
    *,
    dedupe: bool = True,
    create_tables: bool = True,
    dry_run: bool = False,
    progress_callback: ImportProgressCallback | None = None,
) -> list[TableImportResult]:
    config_path = config_path.expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config path does not exist: {config_path}")
    if not config_path.is_dir():
        raise NotADirectoryError(f"Config path is not a directory: {config_path}")

    if create_tables and not dry_run:
        config_tables = [spec.table for spec in CONFIG_IMPORT_SPECS]
        Base.metadata.create_all(session.get_bind(), tables=config_tables)

    results: list[TableImportResult] = []
    table_count = len(CONFIG_IMPORT_SPECS)
    _emit_progress(
        progress_callback,
        phase="start",
        table_name=None,
        table_index=0,
        table_count=table_count,
        detail=f"Preparing config import for {table_count} table(s)",
        dry_run=dry_run,
    )
    for index, spec in enumerate(CONFIG_IMPORT_SPECS, start=1):
        try:
            path = spec.resolve_path(config_path)
        except FileNotFoundError:
            if spec.optional:
                continue
            raise
        results.append(
            _sync_table(
                session,
                spec,
                path,
                table_index=index,
                table_count=table_count,
                dedupe=dedupe,
                dry_run=dry_run,
                progress_callback=progress_callback,
            )
        )
    _emit_progress(
        progress_callback,
        phase="complete",
        table_name=None,
        table_index=table_count,
        table_count=table_count,
        detail="Config import complete",
        dry_run=dry_run,
    )
    return results


__all__ = [
    "CONFIG_IMPORT_SPECS",
    "ImportProgressEvent",
    "TableImportResult",
    "TableImportSpec",
    "_clean_row",
    "import_config_directory",
]
