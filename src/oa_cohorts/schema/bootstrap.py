"""Bringing a dashboard database under migration management.

fresh database
    None of the oa-cohorts tables exist. Run the migrations from base.
existing database, never stamped
    The schema is already there. Check it matches the models and adopt it by
    stamping the head — no DDL. If it does *not* match, refuse.
already managed
    Report the status and leave it alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy as sa
from oa_configurator import get_logger

from .check import SchemaCheckResult, check_schema
from .metadata import owned_table_names, owned_tables
from .runner import current_revision, head_revision, stamp, upgrade

logger = get_logger(__name__)


@dataclass(frozen=True)
class SchemaBootstrapResult:
    table_count: int
    created_tables: tuple[str, ...]
    existing_tables: tuple[str, ...]
    action: str = "unchanged"
    revision: str | None = None
    check: SchemaCheckResult | None = None
    messages: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        return self.action == "blocked"


def _existing_owned_tables(engine: sa.Engine, *, schema: str | None) -> tuple[str, ...]:
    inspector = sa.inspect(engine)
    present = set(inspector.get_table_names(schema=schema))
    return tuple(sorted(owned_table_names() & present))


def bootstrap_query_schema(
    engine: sa.Engine,
    *,
    schema: str | None = None,
    adopt_on_drift: bool = False,
    revision: str | None = None,
) -> SchemaBootstrapResult:
    """Bring the dashboard database under migration management.

    ``revision`` stamps a named revision instead of head, for a database whose
    schema matches an *earlier* revision than the current models. That is not a
    corner case: a dashboard created by 0.8.7 or earlier has TIMESTAMP report
    date columns and matches ``0001_baseline``, not head. Adopting it means
    ``--revision 0001_baseline`` followed by ``schema upgrade``; stamping head
    would claim migrations had run that never did, and ``0002`` would then never
    be applied.

    ``adopt_on_drift`` forces the stamp even when the check fails. It exists for
    the case where the difference is known and intentional; it repairs nothing.
    Prefer ``revision`` where the difference is simply an unapplied migration.
    """
    all_tables = tuple(sorted(owned_table_names()))
    existing_before = _existing_owned_tables(engine, schema=schema)
    target = revision or "head"
    revision = current_revision(engine)

    if revision is not None:
        return SchemaBootstrapResult(
            table_count=len(all_tables),
            created_tables=(),
            existing_tables=existing_before,
            action="unchanged",
            revision=revision,
            messages=(
                (
                    f"Database is already managed at revision {revision}. "
                    "Use 'oa-cohorts schema upgrade' to apply any pending migrations."
                ),
            ),
        )

    if not existing_before:
        upgrade(engine, "head")
        applied = head_revision()
        created = _existing_owned_tables(engine, schema=schema)
        return SchemaBootstrapResult(
            table_count=len(all_tables),
            created_tables=created,
            existing_tables=(),
            action="created",
            revision=applied,
            messages=(f"Created {len(created)} tables at revision {applied}.",),
        )

    result = check_schema(engine, schema=schema)
    # An explicit revision is the operator asserting which revision this schema
    # matches, so the models are the wrong thing to compare it against -- the
    # difference they show is exactly the migrations still to be applied.
    if not result.is_clean and not adopt_on_drift and target == "head":
        return SchemaBootstrapResult(
            table_count=len(all_tables),
            created_tables=(),
            existing_tables=existing_before,
            action="blocked",
            revision=None,
            check=result,
            messages=(
                (
                    "This database has tables that do not match the models, so it cannot be "
                    "adopted at head. Run 'oa-cohorts schema check' for the detail. If the "
                    "difference is a migration that has not been applied yet -- a database "
                    "from 0.8.7 or earlier has TIMESTAMP report date columns -- adopt the "
                    "revision it does match and upgrade from there:\n"
                    "  oa-cohorts schema bootstrap --revision 0001_baseline\n"
                    "  oa-cohorts schema upgrade\n"
                    "Run 'oa-cohorts schema history' for the revisions. Otherwise reconcile "
                    "the schema and re-run, or use --adopt-on-drift to stamp anyway."
                ),
            ),
        )

    stamp(engine, target)
    applied = current_revision(engine) or head_revision()
    note = f"Adopted the existing schema as revision {applied} without running DDL."
    if target != "head":
        note = f"{note} This is not head; run 'oa-cohorts schema upgrade' to apply the rest."
    elif not result.is_clean:
        note = f"{note} Adopted despite {len(result.changes)} difference(s) because --adopt-on-drift was set."
    return SchemaBootstrapResult(
        table_count=len(all_tables),
        created_tables=(),
        existing_tables=existing_before,
        action="stamped",
        revision=applied,
        check=result if not result.is_clean else None,
        messages=(note,),
    )


def create_owned_tables(engine: sa.Engine) -> tuple[str, ...]:
    """``create_all`` scoped to the oa-cohorts tables.

    Provided for tests and throwaway databases. Production paths should go
    through the migrations so the revision pointer stays meaningful.
    """
    tables = list(owned_tables())
    tables[0].metadata.create_all(engine, tables=tables, checkfirst=True)
    return tuple(table.name for table in tables)


__all__ = ["SchemaBootstrapResult", "bootstrap_query_schema", "create_owned_tables"]
