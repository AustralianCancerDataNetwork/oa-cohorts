from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa

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


@pytest.fixture(scope="session")
def pg_url() -> str:
    """URL of the throwaway Postgres dashboard database, or skip.

    ``resolve_test_database`` reads ``[tools.oa_cohorts].test_dashboard_db``
    directly rather than through ``get_config()``, so a CI runner with no
    production databases configured still resolves it. It skips when the field
    is unset and fails if it resolves to a connection that is not
    ``test_only`` — the guard that keeps this destructive suite off a real
    dashboard database.
    """
    from oa_configurator.pytest_plugin import (
        ensure_test_db_exists,
        ensure_test_user_exists,
        resolve_test_database,
    )

    from oa_cohorts.config import OaCohortsConfig

    url = resolve_test_database(OaCohortsConfig, "test_dashboard_db")
    ensure_test_user_exists(url)
    ensure_test_db_exists(url)
    return url


@pytest.fixture
def pg_engine(pg_url) -> Iterator[sa.Engine]:
    """A Postgres engine over an empty schema, torn down after each test.

    Function-scoped and schema-dropping because every test here is stateful:
    they create tables, stamp revisions and mutate enum types. ``DROP SCHEMA
    public CASCADE`` also clears the enum types and ``alembic_version``, which
    a table-level cleanup would leave behind.
    """
    engine = sa.create_engine(pg_url, future=True)

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
