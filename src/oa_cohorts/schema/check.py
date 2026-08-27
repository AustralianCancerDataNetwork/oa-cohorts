"""Compare the live schema to the models using Alembic's own machinery.

Two things are worth knowing about what it can and cannot see.

*Scope.* ``orm_loader.helpers.Base`` is shared with ``omop_alchemy``, so the
comparison is restricted to the tables oa-cohorts owns. Without that filter it
would propose creating the entire OMOP CDM.

*Enums.* Autogenerate compares column types but never the value set of an
existing enum. Adding a member to a Python enum produces no operation here and
fails at insert time instead. When a label changes, the migration needs the
``ALTER TYPE ... ADD VALUE`` written by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from oa_configurator import get_logger

from .metadata import owned_metadata, owned_table_names, owned_tables

logger = get_logger(__name__)


@dataclass(frozen=True)
class SchemaChange:
    """One operation Alembic would emit to reconcile the database."""

    operation: str
    table: str
    column: str | None
    detail: str

    @property
    def location(self) -> str:
        return f"{self.table}.{self.column}" if self.column else self.table

    def __str__(self) -> str:
        return f"{self.operation} {self.location}: {self.detail}"


@dataclass(frozen=True)
class SchemaCheckResult:
    """Everything the comparison found."""

    changes: tuple[SchemaChange, ...] = ()
    tables_checked: int = 0
    tables_missing: tuple[str, ...] = ()
    dialect: str = ""

    @property
    def is_clean(self) -> bool:
        return not self.changes

    @property
    def is_blocking(self) -> bool:
        """Whether a write command should refuse to run against this schema."""
        return bool(self.changes)

    def sorted_changes(self) -> tuple[SchemaChange, ...]:
        return tuple(sorted(self.changes, key=lambda c: (c.table, c.column or "", c.operation)))

    def summary_line(self) -> str:
        if self.is_clean:
            return f"Schema matches the models ({self.tables_checked} tables checked)."
        return f"Schema drift detected: {len(self.changes)} difference(s) across {self.tables_checked} tables."


def _describe(entry: tuple) -> SchemaChange | None:
    """Turn one Alembic diff tuple into something a human can act on.

    Diff tuples describe how to change the *database* to match the models, so
    ``add_column`` means the column is missing from the database.
    """
    operation = str(entry[0])

    if operation in {"add_table", "remove_table"}:
        table = entry[1]
        name = getattr(table, "name", str(table))
        if operation == "add_table":
            return SchemaChange(operation, name, None, "Table is missing from the database.")
        return SchemaChange(operation, name, None, "Table is in the database but not in the models.")

    if operation in {"add_column", "remove_column"}:
        _schema, table_name, column = entry[1], entry[2], entry[3]
        column_name = getattr(column, "name", str(column))
        if operation == "add_column":
            detail = f"Column is missing from the database (model declares {column.type})."
        else:
            detail = "Column is in the database but not in the models."
        return SchemaChange(operation, str(table_name), str(column_name), detail)

    if operation.startswith("modify_"):
        # ('modify_<attr>', schema, table, column, opts, database_value, model_value)
        _schema, table_name, column_name = entry[1], entry[2], entry[3]
        database_value, model_value = entry[5], entry[6]
        attribute = operation.removeprefix("modify_")
        detail = f"Database has {attribute}={database_value!r}, models declare {model_value!r}."
        return SchemaChange(operation, str(table_name), str(column_name), detail)

    if operation in {"add_constraint", "remove_constraint", "add_index", "remove_index"}:
        obj = entry[1]
        table = getattr(getattr(obj, "table", None), "name", "") or ""
        columns = ", ".join(getattr(column, "name", str(column)) for column in getattr(obj, "columns", []))
        present = "missing from the database" if operation.startswith("add_") else "in the database but not in the models"
        kind = "Index" if "index" in operation else "Constraint"
        return SchemaChange(operation, str(table), columns or None, f"{kind} on ({columns}) is {present}.")

    logger.debug("Unrecognised alembic diff entry: %r", entry)
    return None


def _include_object(obj: Any, name: str, type_: str, reflected: bool, compare_to: Any) -> bool:
    """Restrict the comparison to the tables oa-cohorts owns."""
    if type_ == "table":
        return name in owned_table_names()
    return True


def check_schema(engine: sa.Engine, *, schema: str | None = None) -> SchemaCheckResult:
    """Compare the dashboard schema to the models. Never writes DDL."""
    tables = owned_tables()
    target = owned_metadata()
    inspector = sa.inspect(engine)
    present = set(inspector.get_table_names(schema=schema))
    missing = tuple(sorted(owned_table_names() - present))

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": False,
                "include_object": _include_object,
                "target_metadata": target,
            },
        )
        diffs = compare_metadata(context, target)

    changes: list[SchemaChange] = []
    for diff in diffs:
        # modify_* operations arrive as a list of tuples; everything else is one.
        for entry in diff if isinstance(diff, list) else [diff]:
            change = _describe(tuple(entry))
            if change is not None:
                changes.append(change)

    result = SchemaCheckResult(
        changes=tuple(changes),
        tables_checked=len(tables),
        tables_missing=missing,
        dialect=engine.dialect.name,
    )

    if not result.is_clean:
        logger.warning(
            "Schema drift on %s: %d difference(s) across %s",
            engine.url.render_as_string(hide_password=True),
            len(result.changes),
            ", ".join(sorted({change.table for change in result.changes})),
        )
    return result


def is_uninitialised(result: SchemaCheckResult) -> bool:
    """Whether the database simply has none of the oa-cohorts tables yet.

    Worth distinguishing: a brand-new database should be told to bootstrap, not
    warned that it has drifted.
    """
    return len(result.tables_missing) == result.tables_checked > 0


__all__ = [
    "SchemaChange",
    "SchemaCheckResult",
    "check_schema",
    "is_uninitialised",
]
