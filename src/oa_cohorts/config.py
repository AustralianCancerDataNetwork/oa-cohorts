from __future__ import annotations

from typing import Annotated, Any, ClassVar

import sqlalchemy as sa
from oa_configurator import (
    CDMDatabaseConfig,
    ConfigurationError,
    GenericDatabaseConfig,
    PackageConfigBase,
    RefTo,
    ResolvedCDMDatabase,
    Resolver,
    StackConfig,
    load_stack_config,
)

CONVENTIONAL_CDM_DB = "cdm_db"


class OaCohortsConfig(PackageConfigBase):
    """Package-level configuration surface for oa-cohorts.

    ``dashboard_db`` holds the report, indicator and measure configuration 
    oa-cohorts owns and migrates;
    
    ``cdm_db`` is the OMOP CDM the cohorts are executed against, owned by
    omop-alchemy. 
    
    They may point at the same physical server, but oa-cohorts only ever 
    writes DDL to the first.
    """

    tool_name: ClassVar[str] = "oa_cohorts"
    extra_logging_namespaces: ClassVar[tuple[str, ...]] = ("orm_loader", "omop_constructs")

    dashboard_db: Annotated[str, RefTo(GenericDatabaseConfig)] = "dashboard_db"
    cdm_db: Annotated[str | None, RefTo(CDMDatabaseConfig)] = None
    test_dashboard_db: Annotated[
        str | None, RefTo(GenericDatabaseConfig, is_test=True)
    ] = None


def _resolver_and_config(stack: StackConfig | None) -> tuple[Resolver, OaCohortsConfig]:
    """Load the stack (unless supplied) and resolve this package's section.

    Shared by the two engine factories below, which both need the resolver *and*
    the resolved config. omop-alchemy and omop-constructs each have only one such
    factory, so neither has an equivalent helper.

    Deliberately does not translate ``FileNotFoundError`` from
    ``load_stack_config`` into something friendlier, unlike omop-alchemy's
    ``get_cdm_context``. ``cli.runtime.resolve_engine`` catches that exact
    exception to fall back to ``$ENGINE``, so wrapping it would silently disable
    the local-override path.
    """
    resolver = Resolver(stack if stack is not None else load_stack_config())
    return resolver, resolver.resolve_package_config(OaCohortsConfig)


def create_dashboard_engine(
    stack: StackConfig | None = None,
    **engine_kwargs: Any,
) -> sa.Engine:
    """Create a SQLAlchemy engine for the dashboard configuration database."""
    resolver, config = _resolver_and_config(stack)
    try:
        return resolver.resolve_engine(config.dashboard_db, **engine_kwargs)
    except KeyError as exc:
        raise ConfigurationError(
            f"OaCohortsConfig could not resolve database {config.dashboard_db!r}. "
            "Run 'omop-config configure oa_cohorts' to provision the dashboard database, "
            "or provide --database-url for a per-command override."
        ) from exc


def create_cdm_engine(stack: StackConfig | None = None) -> sa.Engine:
    """Create the primary engine for the shared OMOP CDM database.

    Only the *resolved database* crosses the boundary. omop-alchemy's own
    ``OmopAlchemyConfig`` is documented as internal to that package, so this
    resolves oa-cohorts' own ``cdm_db`` field and hands over the result.

    A colocated vocabulary is required because query compilation reads both
    vocabulary models and omop-constructs materialized views.
    """
    resolver, config = _resolver_and_config(stack)
    name = config.cdm_db or CONVENTIONAL_CDM_DB
    try:
        resolved = resolver.resolve_database(name)
    except KeyError as exc:
        raise ConfigurationError(
            f"OaCohortsConfig could not resolve CDM database {name!r}. "
            "Configure 'omop_alchemy' first so oa-cohorts can reuse the shared CDM "
            "database, or point oa_cohorts at it directly with "
            "'omop-config configure oa_cohorts --cdm-db <name>'."
        ) from exc

    # RefTo(CDMDatabaseConfig) already guarantees the kind for an explicitly
    # configured cdm_db, but not for the CONVENTIONAL_CDM_DB fallback above,
    # which resolves a name nobody validated against this field.
    if not isinstance(resolved, ResolvedCDMDatabase):
        raise ConfigurationError(
            f"OaCohortsConfig requires a CDM database, but {name!r} resolves to "
            f"{type(resolved).__name__}. Set 'omop-config configure oa_cohorts "
            "--cdm-db <name>' to a database declared kind = \"cdm\"."
        )

    from omop_alchemy.config import create_cdm_engines as _omop_alchemy_cdm_engines
    from omop_constructs.config import require_colocated_vocabulary

    require_colocated_vocabulary(resolved)
    primary, _vocab = _omop_alchemy_cdm_engines(resolved)
    return primary


__all__ = [
    "CONVENTIONAL_CDM_DB",
    "OaCohortsConfig",
    "create_cdm_engine",
    "create_dashboard_engine",
]
