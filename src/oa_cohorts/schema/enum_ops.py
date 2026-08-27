"""Idempotent enum operations for migrations.

Alembic's autogenerate never compares the value set of an existing enum, so a
label added to a Python enum has to be written into a migration by hand. This
makes that safe to write once and re-run: :func:`sync_enum_labels` adds only the
labels that are missing, and does nothing at all on a backend without native
enum types.

Because it is idempotent it can sit in any revision, including one that also
creates the type — the call is simply a no-op there. That means a revision
touching an enum does not need to care whether it is running against a fresh
database or an existing one.

Imported by migration files, so treat the signature as frozen: changing it would
break revisions already written against it.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

#: ``ALTER TYPE ... ADD VALUE`` could not run inside a transaction block before
#: Postgres 12, and Alembic wraps migrations in one. Older servers need the
#: statement issued outside the migration's transaction instead.
_MIN_TRANSACTIONAL_PG = (12,)


def sync_enum_labels(enum_name: str, labels: Sequence[str], *, schema: str | None = None) -> None:
    """Add any of ``labels`` the Postgres enum ``enum_name`` does not already have.

    Idempotent, and a no-op on backends without native enum types — SQLite
    stores these columns as plain ``VARCHAR``, so there is nothing to alter.

    Labels are only ever added. Postgres cannot remove a value from an enum
    type, and a label still present in the database but no longer in the models
    is harmless for writes.

    Parameters
    ----------
    enum_name
        The Postgres type name, e.g. ``"ruletemporality"``. SQLAlchemy derives
        it from the Python enum class name, lowercased.
    labels
        The full label set the models expect. Passing all of them rather than
        just the new one keeps the revision reproducible.
    schema
        Schema holding the type. ``None`` uses the search path.
    """
    context = op.get_context()
    if context.dialect.name != "postgresql":
        return

    _assert_transactional_alter_type(context)

    preparer = context.dialect.identifier_preparer
    qualified = preparer.quote(enum_name)
    if schema:
        qualified = f"{preparer.quote_schema(schema)}.{qualified}"

    for label in labels:
        quoted = label.replace("'", "''")
        op.execute(f"ALTER TYPE {qualified} ADD VALUE IF NOT EXISTS '{quoted}'")


def _assert_transactional_alter_type(context: object) -> None:
    """Fail clearly on a Postgres too old to alter an enum inside a transaction."""
    connection = getattr(context, "bind", None)
    # Offline (--sql) mode has no live connection; the version cannot be checked
    # and the emitted SQL is for a human to review anyway.
    if connection is None:
        return

    version = getattr(connection.dialect, "server_version_info", None)
    if version is not None and version < _MIN_TRANSACTIONAL_PG:
        raise RuntimeError(
            "ALTER TYPE ... ADD VALUE cannot run inside a transaction on Postgres "
            f"{'.'.join(str(part) for part in version)}. Upgrade the server, or apply "
            "the enum change manually with 'oa-cohorts schema sql' output run outside "
            "a transaction."
        )


__all__ = ["sync_enum_labels"]
