from __future__ import annotations

import psycopg.errors as pg_errors
import sqlalchemy as sa


def fake_undefined_table_error(relation_name: str) -> sa.exc.ProgrammingError:
    orig = pg_errors.UndefinedTable(f'relation "{relation_name}" does not exist')
    return sa.exc.ProgrammingError("SELECT 1", {}, orig)


def fake_undefined_column_error(column_name: str) -> sa.exc.ProgrammingError:
    orig = pg_errors.UndefinedColumn(f'column "{column_name}" does not exist')
    return sa.exc.ProgrammingError("SELECT 1", {}, orig)


def fake_permission_denied_error() -> sa.exc.ProgrammingError:
    orig = pg_errors.InsufficientPrivilege("permission denied for table measure")
    return sa.exc.ProgrammingError("SELECT 1", {}, orig)
