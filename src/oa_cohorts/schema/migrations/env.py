"""Alembic environment for the oa-cohorts dashboard schema.

Driven programmatically from :mod:`oa_cohorts.schema.runner`; there is no
``alembic.ini`` at the repository root and no ``sqlalchemy.url`` in config.
The engine or connection arrives through ``config.attributes``.

The critical piece here is ``include_object``. ``Base.metadata`` is shared with
omop_alchemy, so without this filter autogenerate would propose creating the
entire OMOP CDM and would report every CDM table in the dashboard database as
an unmanaged extra.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import context
from orm_loader.helpers import Base

from oa_cohorts.schema.metadata import load_query_metadata, owned_table_names

config = context.config

load_query_metadata()
target_metadata = Base.metadata


def include_object(obj: Any, name: str, type_: str, reflected: bool, compare_to: Any) -> bool:
    """Restrict every operation to the 15 tables oa-cohorts owns."""
    if type_ == "table":
        return name in owned_table_names()
    return True


def include_name(name: str | None, type_: str, parent_names: Any) -> bool:
    """Keep reflection from walking the CDM tables during autogenerate."""
    if type_ == "table_name":
        return name is None or name in owned_table_names()
    return True


_COMMON_OPTIONS: dict[str, Any] = {
    "target_metadata": target_metadata,
    "include_object": include_object,
    "include_name": include_name,
    "include_schemas": False,
    "compare_type": True,
    "compare_server_default": False,
}


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it, for review before applying."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_COMMON_OPTIONS,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: sa.Connection) -> None:
    context.configure(connection=connection, **_COMMON_OPTIONS)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if isinstance(connection, sa.Connection):
        _run_with_connection(connection)
        return

    engine = config.attributes.get("engine")
    if engine is None:
        url = config.get_main_option("sqlalchemy.url", None)
        if not url:
            raise RuntimeError(
                "No engine, connection or URL was supplied to the alembic environment. "
                "Use oa_cohorts.schema.runner rather than invoking alembic directly."
            )
        engine = sa.create_engine(url)

    with engine.connect() as new_connection:
        _run_with_connection(new_connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
