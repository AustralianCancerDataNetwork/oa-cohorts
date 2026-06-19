from __future__ import annotations

from typing import Any, ClassVar

from oa_configurator import (
    ConfigurationError,
    PackageConfigBase,
    Resolver,
    ResourceSpec,
    StackConfig,
    load_stack_config,
)


class OaCohortsConfig(PackageConfigBase):
    """Package-level configuration surface for oa-cohorts."""

    DASHBOARD_DB: ClassVar[ResourceSpec] = ResourceSpec(
        semantic_name="dashboard_db",
        display_name="Dashboard Config Database",
        description=(
            "Database where oa-cohorts stores its report, indicator, and measure configuration. "
            "Can point to the same physical database as the CDM or a dedicated application database."
        ),
        connection_name_hint="dashboard",
        is_cdm_database=False,
        cdm_schema_default="public",
    )
    CDM_DB: ClassVar[ResourceSpec] = ResourceSpec(
        semantic_name="cdm_db",
        display_name="OMOP CDM Database",
        description="OMOP CDM database used for cohort execution. Typically shared with omop-alchemy.",
        connection_name_hint="cdm",
    )

    tool_name: ClassVar[str] = "oa_cohorts"
    # Only the dashboard_db is listed here — it is the resource oa-cohorts owns and that
    # omop-config configure should prompt for. cdm_db is an external dependency resolved
    # at runtime via resolve_cdm_resource_name(), which follows omop_constructs / omop_alchemy
    # configuration. Listing cdm_db here would make it selectable as oa_cohorts.default_resource,
    # collapsing the dashboard/CDM separation.
    required_resources: ClassVar[tuple[str, ...]] = (DASHBOARD_DB.semantic_name,)
    owned_resources: ClassVar[tuple[ResourceSpec, ...]] = (DASHBOARD_DB,)
    extra_logging_namespaces: ClassVar[tuple[str, ...]] = ("orm_loader", "omop_constructs")

    @classmethod
    def get_engine(cls, resource_name: str | None = None, **engine_kwargs: Any) -> Any:
        """Create a SQLAlchemy engine for the dashboard config database."""
        stack = load_stack_config()
        tool = stack.tools.get(cls.tool_name)
        name = resource_name or (tool.default_resource if tool else None) or cls.DASHBOARD_DB.semantic_name
        try:
            return Resolver(stack).resolve_resource(name).create_engine(**engine_kwargs)
        except KeyError as exc:
            raise ConfigurationError(
                f"OaCohortsConfig could not resolve resource {name!r}. "
                "Run 'omop-config configure oa_cohorts' to provision the dashboard resource, "
                "or provide --database-url for a per-command override."
            ) from exc

    @classmethod
    def get_cdm_engine(cls, **engine_kwargs: Any) -> Any:
        """Create a SQLAlchemy engine for the shared OMOP CDM database."""
        stack = load_stack_config()
        name = resolve_cdm_resource_name(stack)
        try:
            return Resolver(stack).resolve_resource(name).create_engine(**engine_kwargs)
        except KeyError as exc:
            raise ConfigurationError(
                f"OaCohortsConfig could not resolve CDM resource {name!r}. "
                "Configure 'omop_alchemy' or 'omop_constructs' first so oa-cohorts can "
                "reuse the shared CDM resource, or run 'omop-config configure oa_cohorts'."
            ) from exc


def resolve_cdm_resource_name(stack: StackConfig) -> str:
    """Return the shared CDM resource name for cohort execution.

    Searches tool default_resource settings in priority order:
    omop_constructs → omop_alchemy → cdm_db (canonical fallback).

    Note: oa_cohorts.default_resource is intentionally excluded — it points to
    the dashboard_db resource, not the CDM.
    """
    available: set[str] = set(stack.resource_names())
    if stack.active_profile and stack.active_profile in stack.profiles:
        available |= set(stack.profiles[stack.active_profile].resources)

    seen: set[str] = set()
    candidates: list[str] = []
    for name in (
        *(
            tool.default_resource
            for tool_name in ("omop_constructs", "omop_alchemy")
            if (tool := stack.tools.get(tool_name)) is not None and tool.default_resource
        ),
        OaCohortsConfig.CDM_DB.semantic_name,
    ):
        if name not in seen:
            seen.add(name)
            candidates.append(name)

    for resource_name in candidates:
        resolved_name = stack.resource_aliases.get(resource_name, resource_name)
        if resolved_name in available:
            return resource_name

    alias_hint = (
        "\nTip: if you named your resource differently, add:\n"
        '  [resource_aliases]\n  cdm_db = "your-resource-name"'
    )
    raise ConfigurationError(
        "OaCohortsConfig requires a resolvable CDM resource for cohort execution. "
        f"Tried: {candidates}\n"
        f"Available: {sorted(available) or '(none)'}\n"
        "Configure 'omop_alchemy' or 'omop_constructs' first so oa-cohorts can "
        "reuse the shared CDM resource."
        + alias_hint
    )


__all__ = ["OaCohortsConfig", "resolve_cdm_resource_name"]
