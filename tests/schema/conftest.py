from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from oa_configurator.testing import isolated_test_database

from oa_cohorts.config import OaCohortsConfig
from oa_cohorts.schema.metadata import owned_table_names, owned_tables


@pytest.fixture
def sqlite_engine(tmp_path) -> Iterator[sa.Engine]:
    """A file-backed SQLite database.

    On disk rather than in memory because reflection and Alembic each open
    their own connections, and ``sqlite://`` gives every connection its own
    empty database.
    """
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'schema.db'}")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _reset_owned_table_cache() -> Iterator[None]:
    """Stop a test that mutates a model type from leaking into the next."""
    yield
    owned_tables.cache_clear()
    owned_table_names.cache_clear()


@pytest.fixture
def pg_db(request):
    with isolated_test_database(
        OaCohortsConfig,
        "test_dashboard_db",
        request=request,
    ) as database:
        yield database


@pytest.fixture
def pg_engine(pg_db) -> Iterator[sa.Engine]:
    """A Postgres engine over an empty schema, torn down after each test.

    Function-scoped and schema-dropping because every test here is stateful:
    they create tables, stamp revisions and mutate enum types. ``DROP SCHEMA
    public CASCADE`` also clears the enum types and ``alembic_version``, which
    a table-level cleanup would leave behind.
    """
    engine = pg_db.committing_engine

    def _reset() -> None:
        with engine.begin() as connection:
            connection.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(sa.text("CREATE SCHEMA public"))

    _reset()
    try:
        yield engine
    finally:
        try:
            _reset()
        finally:
            engine.dispose()
