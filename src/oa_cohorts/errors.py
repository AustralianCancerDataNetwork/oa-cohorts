from __future__ import annotations

import re

import psycopg.errors as pg_errors
import sqlalchemy as sa

_UNDEFINED_RELATION_RE = re.compile(r'relation "([^"]+)" does not exist')
_UNDEFINED_COLUMN_RE = re.compile(r'column "([^"]+)" does not exist')


class OaCohortsSchemaError(RuntimeError):
    """Base for 'the database schema doesn't match what this code expects.'"""


class MissingRelationError(OaCohortsSchemaError):
    """A table or materialized view this code depends on does not exist yet.

    Concrete subclasses correspond to who owns/creates the missing relation,
    and therefore what actually fixes it.
    """

    #: Overridden per subclass with the specific remediation for that owner.
    _REMEDIATION: str = "oa-cohorts does not recognize this relation, so it cannot suggest how to create it."

    def __init__(self, relation_name: str, *, context: str):
        self.relation_name = relation_name
        self.context = context
        super().__init__(
            f"{context} needs the relation '{relation_name}', which does not exist in this "
            f"database yet. {self._REMEDIATION}"
        )


class MissingMaterializedViewError(MissingRelationError):
    """The missing relation is a CDM materialized view owned by omop-constructs."""

    _REMEDIATION = (
        "It is a CDM materialized view built by omop-constructs, separately from oa-cohorts' "
        "own schema bootstrap. Create it (and any other registered CDM constructs) with: "
        "`omop_constructs.bootstrap.get_complete_construct_registry().create_all(engine)`."
    )


class MissingCdmTableError(MissingRelationError):
    """The missing relation is a base OMOP CDM table owned by omop-alchemy."""

    _REMEDIATION = "It is a base OMOP CDM table owned by omop-alchemy. Create it with: `omop-alchemy create-missing-tables`."


class SchemaNotBootstrappedError(MissingRelationError):
    """The missing relation is one of oa-cohorts' own dashboard config tables."""

    _REMEDIATION = (
        "It is one of oa-cohorts' own dashboard config tables. Create it with: "
        "`oa-cohorts schema bootstrap`."
    )


class SchemaDriftError(OaCohortsSchemaError):
    """A column this code expects is missing from an existing relation.

    Unlike a missing relation, PostgreSQL does not report which relation was
    being queried for an undefined column in a plain SELECT, so this can only
    name the column, not point at a specific owner/remediation the way
    :class:`MissingRelationError` can.
    """

    def __init__(self, column_name: str, *, context: str):
        self.column_name = column_name
        self.context = context
        super().__init__(
            f"{context} needs column '{column_name}', which does not exist in this database. "
            "The schema is out of sync with the installed package versions -- most likely a "
            "pending migration. Run `oa-cohorts schema check` (for oa-cohorts' own tables) or "
            "`omop-alchemy reconcile-schema` (for CDM tables) to see what's out of date."
        )


def _classify_relation(relation_name: str) -> type[MissingRelationError] | None:
    """Return which :class:`MissingRelationError` a relation name maps to, or None if unrecognized."""
    from .schema.metadata import owned_table_names

    if relation_name in owned_table_names():
        return SchemaNotBootstrappedError

    from orm_loader.helpers import Base

    if relation_name in Base.metadata.tables:
        return MissingCdmTableError

    try:
        from omop_constructs.bootstrap import list_cdm_matview_names

        matview_names = list_cdm_matview_names()
    except Exception:
        # Loading the full construct registry imports every registered CDM
        # construct module -- a heavier, more side-effecting operation than
        # the other two checks. If it fails for any reason, this classifier
        # must not itself raise a new, unrelated error in place of the
        # original database error it was trying to explain; fall through to
        # "not recognized" instead.
        return None

    if relation_name in matview_names:
        return MissingMaterializedViewError

    return None


def reraise_schema_error(exc: sa.exc.DBAPIError, *, context: str) -> None:
    """Re-raise *exc* as a typed :class:`OaCohortsSchemaError` if it's a recognized case.

    Returns normally (does nothing) otherwise -- callers are expected to
    re-raise the original exception themselves in that case, e.g.::

        try:
            db.execute(sql)
        except sa.exc.DBAPIError as exc:
            reraise_schema_error(exc, context="...")
            raise
    """
    orig = exc.orig
    message = str(orig)

    if isinstance(orig, pg_errors.UndefinedTable):
        match = _UNDEFINED_RELATION_RE.search(message)
        if match is None:
            return
        error_cls = _classify_relation(match.group(1))
        if error_cls is not None:
            raise error_cls(match.group(1), context=context) from exc
        return

    if isinstance(orig, pg_errors.UndefinedColumn):
        match = _UNDEFINED_COLUMN_RE.search(message)
        if match is not None:
            raise SchemaDriftError(match.group(1), context=context) from exc


__all__ = [
    "MissingCdmTableError",
    "MissingMaterializedViewError",
    "MissingRelationError",
    "OaCohortsSchemaError",
    "SchemaDriftError",
    "SchemaNotBootstrappedError",
    "reraise_schema_error",
]
