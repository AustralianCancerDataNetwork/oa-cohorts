from __future__ import annotations

from typing import ClassVar

from oa_configurator import PackageConfigBase


class OaCohortsConfig(PackageConfigBase):
    """Package-level configuration surface for oa-cohorts."""

    tool_name: ClassVar[str] = "oa_cohorts"
    required_resources: ClassVar[tuple[str, ...]] = ("cdm_db",)
    extra_logging_namespaces: ClassVar[tuple[str, ...]] = ("orm_loader", "omop_constructs")


__all__ = ["OaCohortsConfig"]
