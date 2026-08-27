"""CLI glue for schema verification and migration.

The comparison itself is Alembic's (:mod:`oa_cohorts.schema.check`); this module
only decides when a command should be warned or stopped, and hands the result to
the renderers.
"""

from __future__ import annotations

from enum import StrEnum

import sqlalchemy as sa
import typer
from rich.console import Console
from sqlalchemy.exc import SQLAlchemyError

from ..schema import (
    SchemaBootstrapResult,
    SchemaCheckResult,
    bootstrap_query_schema,
    check_schema,
    is_uninitialised,
)


class GuardMode(StrEnum):
    """How much a command cares about drift.

    ``read``
        Warn and continue. A summary query against a drifted schema is worth
        seeing, with the caveat attached.
    ``write``
        Warn and stop. Importing config into a schema that does not match the
        models is how a half-loaded configuration happens, and re-running does
        not fix it.
    ``off``
        No check. Used by the schema commands themselves, which are the remedy
        rather than a consumer.
    """

    read = "read"
    write = "write"
    off = "off"


def guard_schema(
    console: Console,
    engine: sa.Engine,
    *,
    mode: GuardMode,
    ignore_drift: bool = False,
    auto_bootstrap: bool = False,
    schema: str | None = None,
) -> SchemaCheckResult | None:
    """Check the schema and warn — or stop — before the command does its work.

    Never migrates an existing schema. ``auto_bootstrap`` covers the one case
    that is initialisation rather than migration: a database with none of the
    oa-cohorts tables, where the command was already going to create them. That
    path builds them through the migrations so the database ends up stamped,
    instead of leaving an unmanaged ``create_all`` result behind. A database
    that already has tables is never touched.

    A failure inside the check itself is reported and does not stop the
    command: a broken check must not take the CLI down with it.
    """
    if mode is GuardMode.off:
        return None

    # Imported here so ui.py can import GuardMode from this module without a cycle.
    from .ui import (
        render_schema_auto_bootstrap,
        render_schema_bootstrap_result,
        render_schema_drift_warning,
        render_schema_guard_unavailable,
    )

    try:
        result = check_schema(engine, schema=schema)
    except SQLAlchemyError as exc:
        console.print(render_schema_guard_unavailable(str(exc)))
        return None

    if auto_bootstrap and is_uninitialised(result):
        console.print(render_schema_auto_bootstrap())
        bootstrapped = bootstrap_query_schema(engine, schema=schema)
        console.print(render_schema_bootstrap_result(bootstrapped))
        if bootstrapped.blocked:
            raise typer.Exit(code=1)
        return check_schema(engine, schema=schema)

    if result.is_clean:
        return result

    blocking = mode is GuardMode.write and result.is_blocking and not ignore_drift
    console.print(
        render_schema_drift_warning(
            result,
            blocking=blocking,
            overridden=ignore_drift and result.is_blocking,
            uninitialised=is_uninitialised(result),
        )
    )
    if blocking:
        raise typer.Exit(code=1)
    return result


def explain_schema_failure(console: Console, engine: sa.Engine | None, exc: Exception) -> bool:
    """After a database error, say whether schema drift explains it.

    Returns whether a report was printed.
    """
    if engine is None or not isinstance(exc, SQLAlchemyError):
        return False

    from .ui import render_schema_check_result

    try:
        result = check_schema(engine)
    except SQLAlchemyError:
        return False

    if result.is_clean:
        return False

    console.print(render_schema_check_result(result, title="Likely cause: schema drift"))
    return True


__all__ = [
    "GuardMode",
    "SchemaBootstrapResult",
    "bootstrap_query_schema",
    "explain_schema_failure",
    "guard_schema",
]
