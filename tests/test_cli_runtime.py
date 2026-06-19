from __future__ import annotations

import sqlalchemy as sa
import pytest

from oa_configurator import ConfigurationError, DatabaseConfig, ResourceConfig, StackConfig, ToolConfig
from oa_cohorts.config import OaCohortsConfig, resolve_cdm_resource_name
from oa_cohorts.cli.runtime import resolve_engine


def test_resolve_engine_uses_explicit_database_url(monkeypatch):
    monkeypatch.delenv("ENGINE", raising=False)

    def _should_not_be_called(cls, **engine_kwargs):
        raise AssertionError("config should not be used")

    monkeypatch.setattr(OaCohortsConfig, "get_engine", classmethod(_should_not_be_called))

    def _should_not_be_called(cls, **engine_kwargs):
        raise AssertionError("config should not be used")

    monkeypatch.setattr(OaCohortsConfig, "get_engine", classmethod(_should_not_be_called))

    engine, resolved_url = resolve_engine(database_url="sqlite://")

    assert isinstance(engine, sa.Engine)
    assert resolved_url == "sqlite://"


def test_resolve_engine_uses_configured_stack_engine(monkeypatch):
    monkeypatch.delenv("ENGINE", raising=False)

    def fake_get_engine(cls, **engine_kwargs):
        return sa.create_engine("sqlite://", **engine_kwargs)

    monkeypatch.setattr(OaCohortsConfig, "get_engine", classmethod(fake_get_engine))

    engine, resolved_url = resolve_engine(database_url=None)

    assert isinstance(engine, sa.Engine)
    assert resolved_url is None


def test_resolve_engine_uses_engine_env_when_config_missing(monkeypatch):
    monkeypatch.setenv("ENGINE", "sqlite://")

    def _raise_not_found(cls, **engine_kwargs):
        raise FileNotFoundError("missing config")

    monkeypatch.setattr(OaCohortsConfig, "get_engine", classmethod(_raise_not_found))

    engine, resolved_url = resolve_engine(database_url=None)

    assert isinstance(engine, sa.Engine)
    assert resolved_url == "sqlite://"


def test_resolve_engine_fails_clearly_without_config_or_override(monkeypatch):
    monkeypatch.delenv("ENGINE", raising=False)

    def _raise_not_found(cls, **engine_kwargs):
        raise FileNotFoundError("missing config")

    monkeypatch.setattr(OaCohortsConfig, "get_engine", classmethod(_raise_not_found))

    with pytest.raises(FileNotFoundError, match="No oa-cohorts dashboard database configured"):
        resolve_engine(database_url=None)


def test_resolve_engine_raises_configuration_error_for_missing_resource(monkeypatch):
    monkeypatch.delenv("ENGINE", raising=False)

    def _raise_configuration_error(cls, **engine_kwargs):
        raise ConfigurationError("dashboard_db resource not found")

    monkeypatch.setattr(OaCohortsConfig, "get_engine", classmethod(_raise_configuration_error))

    with pytest.raises(ConfigurationError, match="dashboard_db resource not found"):
        resolve_engine(database_url=None)


def test_resolve_engine_does_not_use_env_fallback_for_configuration_error(monkeypatch):
    monkeypatch.setenv("ENGINE", "sqlite:///fallback.db")

    def _raise_configuration_error(cls, **engine_kwargs):
        raise ConfigurationError("dashboard_db resource not configured")

    monkeypatch.setattr(OaCohortsConfig, "get_engine", classmethod(_raise_configuration_error))

    with pytest.raises(ConfigurationError):
        resolve_engine(database_url=None)


def test_oa_cohorts_config_owns_dashboard_resource() -> None:
    assert OaCohortsConfig.owned_resources == (OaCohortsConfig.DASHBOARD_DB,)


def test_oa_cohorts_config_required_resources_is_dashboard_only() -> None:
    # cdm_db is intentionally excluded so it cannot be selected as oa_cohorts.default_resource
    assert OaCohortsConfig.required_resources == (OaCohortsConfig.DASHBOARD_DB.semantic_name,)
    assert OaCohortsConfig.CDM_DB.semantic_name not in OaCohortsConfig.required_resources


def test_oa_cohorts_config_does_not_own_cdm_resource() -> None:
    owned_names = {spec.semantic_name for spec in OaCohortsConfig.owned_resources}
    assert OaCohortsConfig.CDM_DB.semantic_name not in owned_names


def test_resource_resolution_falls_back_to_omop_alchemy_default_resource() -> None:
    stack = StackConfig(
        databases={
            "cdm": DatabaseConfig(
                dialect="sqlite",
                database_name=":memory:",
            )
        },
        resources={
            "cdm_db_alt": ResourceConfig(
                database="cdm",
                cdm_schema="omop",
            )
        },
        tools={
            "omop_alchemy": ToolConfig(
                default_resource="cdm_db_alt",
                extra={},
            )
        },
    )

    assert resolve_cdm_resource_name(stack) == "cdm_db_alt"


def test_resource_resolution_prefers_omop_constructs_over_omop_alchemy() -> None:
    stack = StackConfig(
        databases={
            "cdm": DatabaseConfig(
                dialect="sqlite",
                database_name=":memory:",
            )
        },
        resources={
            "cdm_db": ResourceConfig(
                database="cdm",
                cdm_schema="omop",
            ),
            "cdm_reporting": ResourceConfig(
                database="cdm",
                cdm_schema="reporting",
            ),
        },
        tools={
            "omop_alchemy": ToolConfig(
                default_resource="cdm_db",
                extra={},
            ),
            "omop_constructs": ToolConfig(
                default_resource="cdm_reporting",
                extra={},
            ),
        },
    )

    assert resolve_cdm_resource_name(stack) == "cdm_reporting"


def test_resource_resolution_ignores_oa_cohorts_default_resource_for_cdm() -> None:
    """oa_cohorts.default_resource points to the dashboard DB, not CDM."""
    stack = StackConfig(
        databases={
            "cdm": DatabaseConfig(
                dialect="sqlite",
                database_name=":memory:",
            )
        },
        resources={
            "cdm_db": ResourceConfig(
                database="cdm",
                cdm_schema="omop",
            ),
            "dashboard_db": ResourceConfig(
                database="cdm",
                cdm_schema="public",
            ),
        },
        tools={
            "omop_alchemy": ToolConfig(
                default_resource="cdm_db",
                extra={},
            ),
            "oa_cohorts": ToolConfig(
                default_resource="dashboard_db",
                extra={},
            ),
        },
    )

    # oa_cohorts default_resource (dashboard_db) must not be picked for CDM resolution
    assert resolve_cdm_resource_name(stack) == "cdm_db"


def test_resource_resolution_falls_back_to_canonical_cdm_db_name() -> None:
    stack = StackConfig(
        databases={
            "cdm": DatabaseConfig(
                dialect="sqlite",
                database_name=":memory:",
            )
        },
        resources={
            "cdm_db": ResourceConfig(
                database="cdm",
                cdm_schema="omop",
            )
        },
        tools={},
    )

    assert resolve_cdm_resource_name(stack) == "cdm_db"
