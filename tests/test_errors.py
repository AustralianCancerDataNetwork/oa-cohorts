import pytest
import sqlalchemy as sa

import oa_cohorts.query  # noqa: F401 -- registers owned tables on Base.metadata
from oa_cohorts.errors import (
    MissingCdmTableError,
    MissingMaterializedViewError,
    SchemaDriftError,
    SchemaNotBootstrappedError,
    reraise_schema_error,
)

from tests.db_error_helpers import (
    fake_permission_denied_error,
    fake_undefined_column_error,
    fake_undefined_table_error,
)


@pytest.fixture(autouse=True)
def _stub_matview_registry(monkeypatch):
    # The real registry is loaded via omop_constructs.bootstrap.load_construct_families(),
    # which imports every registered CDM construct module as a side effect -- a heavier,
    # process-wide operation than these unit tests should trigger just to classify a
    # relation name. Stub it so every test here is isolated from that (and from whatever
    # else in a full test run may have already touched those modules).
    monkeypatch.setattr(
        "omop_constructs.bootstrap.list_cdm_matview_names", lambda: ["fake_construct_mv"]
    )


def test_reraise_schema_error_classifies_owned_table():
    exc = fake_undefined_table_error("measure")

    with pytest.raises(SchemaNotBootstrappedError, match="measure") as exc_info:
        reraise_schema_error(exc, context="test")

    assert exc_info.value.relation_name == "measure"
    assert exc_info.value.context == "test"
    assert exc_info.value.__cause__ is exc


def test_reraise_schema_error_classifies_cdm_table():
    exc = fake_undefined_table_error("person")

    with pytest.raises(MissingCdmTableError, match="omop-alchemy create-missing-tables"):
        reraise_schema_error(exc, context="test")


def test_reraise_schema_error_classifies_matview():
    exc = fake_undefined_table_error("fake_construct_mv")

    with pytest.raises(MissingMaterializedViewError, match="omop_constructs"):
        reraise_schema_error(exc, context="test")


def test_reraise_schema_error_classifies_undefined_column_as_drift():
    exc = fake_undefined_column_error("some_new_column")

    with pytest.raises(SchemaDriftError, match="some_new_column") as exc_info:
        reraise_schema_error(exc, context="test")

    assert exc_info.value.column_name == "some_new_column"


def test_reraise_schema_error_ignores_unrecognized_relation():
    exc = fake_undefined_table_error("some_unrelated_table")

    reraise_schema_error(exc, context="test")  # does not raise


def test_reraise_schema_error_ignores_other_postgres_error_types():
    exc = fake_permission_denied_error()

    reraise_schema_error(exc, context="test")  # does not raise -- not UndefinedTable/UndefinedColumn


def test_reraise_schema_error_ignores_non_postgres_drivers():
    # e.g. a sqlite3 exception, which is neither psycopg's nor psycopg2's
    # UndefinedTable/UndefinedColumn regardless of its message text.
    exc = sa.exc.ProgrammingError("SELECT 1", {}, Exception('relation "measure" does not exist'))

    reraise_schema_error(exc, context="test")  # does not raise -- can't confirm this is postgres


def test_reraise_schema_error_does_not_crash_if_matview_registry_load_fails(monkeypatch):
    monkeypatch.setattr(
        "omop_constructs.bootstrap.list_cdm_matview_names",
        lambda: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
    )
    exc = fake_undefined_table_error("some_unrelated_table")

    reraise_schema_error(exc, context="test")  # does not raise, and does not mask a new error either
