"""Schema migration and verification for the oa-cohorts dashboard database.

Alembic does both jobs. :mod:`.runner` drives it programmatically so the
revisions ship inside the wheel with no ``alembic.ini``; :mod:`.check` runs its
``compare_metadata`` to report how a live database differs from the models.

Nothing migrates on import or on a normal command. ``oa-cohorts schema
upgrade`` is the only thing that changes DDL.

The one gap worth remembering: autogenerate compares column types but never the
value set of an existing enum, so a label added to a Python enum produces no
revision and fails at insert time. Enum changes need their ``ALTER TYPE``
written by hand — see ``docs/schema_management.md``.
"""

from __future__ import annotations

from .bootstrap import (
    SchemaBootstrapResult,
    bootstrap_query_schema,
    create_owned_tables,
)
from .check import SchemaChange, SchemaCheckResult, check_schema, is_uninitialised
from .metadata import load_query_metadata, owned_table_names, owned_tables
from .runner import (
    MIGRATIONS_PATH,
    MigrationStatus,
    RevisionInfo,
    alembic_config,
    current_revision,
    downgrade,
    head_revision,
    history,
    migration_status,
    revise,
    stamp,
    upgrade,
    upgrade_sql,
)

__all__ = [
    "MIGRATIONS_PATH",
    "MigrationStatus",
    "RevisionInfo",
    "SchemaBootstrapResult",
    "SchemaChange",
    "SchemaCheckResult",
    "alembic_config",
    "bootstrap_query_schema",
    "check_schema",
    "create_owned_tables",
    "current_revision",
    "downgrade",
    "head_revision",
    "history",
    "is_uninitialised",
    "load_query_metadata",
    "migration_status",
    "owned_table_names",
    "owned_tables",
    "revise",
    "stamp",
    "upgrade",
    "upgrade_sql",
]
