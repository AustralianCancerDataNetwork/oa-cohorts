from __future__ import annotations

import pytest
import sqlalchemy as sa
from oa_configurator import (
    CDMDatabaseConfig,
    ConfigurationError,
    ConnectionConfig,
    GenericDatabaseConfig,
    StackConfig,
)

from oa_cohorts.cli import runtime
from oa_cohorts.cli.runtime import resolve_engine
from oa_cohorts.config import (
    CONVENTIONAL_CDM_DB,
    OaCohortsConfig,
    create_cdm_engine,
    create_dashboard_engine,
)


def _stack(*, databases: dict[str, object], tools: dict[str, dict] | None = None) -> StackConfig:
    """A stack config backed by one in-memory SQLite connection."""
    return StackConfig(
        connections={"local": ConnectionConfig(dialect="sqlite", database_name=":memory:")},
        databases=databases,
        tools=tools or {},
    )


DASHBOARD_ONLY = {"dashboard_db": GenericDatabaseConfig(connection="local", schema_name="public")}
DASHBOARD_AND_CDM = {
    **DASHBOARD_ONLY,
    "cdm_db": CDMDatabaseConfig(connection="local", schema_name="omop", vocab_schema="omop"),
    "cdm_reporting": CDMDatabaseConfig(
        connection="local", schema_name="reporting", vocab_schema="omop"
    ),
}


# --------------------------------------------------------------------------
# resolve_engine
# --------------------------------------------------------------------------


def test_resolve_engine_uses_explicit_database_url(monkeypatch):
    monkeypatch.delenv("ENGINE", raising=False)

    def _should_not_be_called(**engine_kwargs):
        raise AssertionError("config should not be used")

    monkeypatch.setattr(runtime, "create_dashboard_engine", _should_not_be_called)

    engine, resolved_url = resolve_engine(database_url="sqlite://")

    assert isinstance(engine, sa.Engine)
    assert resolved_url == "sqlite://"


def test_resolve_engine_uses_configured_stack_engine(monkeypatch):
    monkeypatch.delenv("ENGINE", raising=False)
    monkeypatch.setattr(
        runtime, "create_dashboard_engine", lambda **kw: sa.create_engine("sqlite://", **kw)
    )

    engine, resolved_url = resolve_engine(database_url=None)

    assert isinstance(engine, sa.Engine)
    assert resolved_url is None


def test_resolve_engine_uses_engine_env_when_config_missing(monkeypatch):
    monkeypatch.setenv("ENGINE", "sqlite://")

    def _raise_not_found(**engine_kwargs):
        raise FileNotFoundError("missing config")

    monkeypatch.setattr(runtime, "create_dashboard_engine", _raise_not_found)

    engine, resolved_url = resolve_engine(database_url=None)

    assert isinstance(engine, sa.Engine)
    assert resolved_url == "sqlite://"


def test_resolve_engine_fails_clearly_without_config_or_override(monkeypatch):
    monkeypatch.delenv("ENGINE", raising=False)

    def _raise_not_found(**engine_kwargs):
        raise FileNotFoundError("missing config")

    monkeypatch.setattr(runtime, "create_dashboard_engine", _raise_not_found)

    with pytest.raises(FileNotFoundError, match="No oa-cohorts dashboard database configured"):
        resolve_engine(database_url=None)


def test_resolve_engine_raises_configuration_error_for_missing_database(monkeypatch):
    monkeypatch.delenv("ENGINE", raising=False)

    def _raise_configuration_error(**engine_kwargs):
        raise ConfigurationError("dashboard_db not found")

    monkeypatch.setattr(runtime, "create_dashboard_engine", _raise_configuration_error)

    with pytest.raises(ConfigurationError, match="dashboard_db not found"):
        resolve_engine(database_url=None)


def test_resolve_engine_does_not_use_env_fallback_for_configuration_error(monkeypatch):
    monkeypatch.setenv("ENGINE", "sqlite:///fallback.db")

    def _raise_configuration_error(**engine_kwargs):
        raise ConfigurationError("dashboard_db not configured")

    monkeypatch.setattr(runtime, "create_dashboard_engine", _raise_configuration_error)

    with pytest.raises(ConfigurationError):
        resolve_engine(database_url=None)


# --------------------------------------------------------------------------
# The config surface
# --------------------------------------------------------------------------


def test_field_optionality_matches_what_each_database_is_for() -> None:
    """The asymmetry is deliberate; see OaCohortsConfig's docstring.

    dashboard_db is required because oa-cohorts cannot run without it, so a
    stack lacking one should be rejected up front. cdm_db is optional because
    a dashboard-only deployment is real and resolve_package_config validates
    every RefTo field together -- a default here would fail `schema check`
    over a database it never reads.
    """
    assert OaCohortsConfig.model_fields["dashboard_db"].default == "dashboard_db"
    assert OaCohortsConfig.model_fields["cdm_db"].default is None
    assert OaCohortsConfig.model_fields["test_dashboard_db"].default is None


def test_dashboard_engine_resolves_without_any_cdm_configured() -> None:
    """Every schema, import and summary command must work with no CDM at all.

    This is the reason cdm_db defaults to None rather than "cdm_db":
    resolve_package_config validates every RefTo field at once, so a
    non-None default would make the dashboard path depend on a CDM it
    never reads. dashboard_db keeps its default precisely because the
    opposite is true of it.
    """
    engine = create_dashboard_engine(_stack(databases=DASHBOARD_ONLY))

    assert isinstance(engine, sa.Engine)


def test_dashboard_engine_reports_a_missing_dashboard_database() -> None:
    stack = _stack(
        databases=DASHBOARD_ONLY,
        tools={"oa_cohorts": {"dashboard_db": "not_configured"}},
    )

    with pytest.raises(ConfigurationError, match="not_configured"):
        create_dashboard_engine(stack)


def test_test_dashboard_db_requires_a_test_only_connection() -> None:
    """is_test=True is what keeps the schema-dropping suite off a real database."""
    stack = _stack(
        databases=DASHBOARD_ONLY,
        tools={"oa_cohorts": {"test_dashboard_db": "dashboard_db"}},
    )

    with pytest.raises(ConfigurationError, match="is_test"):
        create_dashboard_engine(stack)


def test_cdm_engine_falls_back_to_the_conventional_name() -> None:
    """Unset, cdm_db shares whatever omop-alchemy named 'cdm_db'."""
    assert CONVENTIONAL_CDM_DB == "cdm_db"
    engine = create_cdm_engine(_stack(databases=DASHBOARD_AND_CDM))

    assert isinstance(engine, sa.Engine)


def test_cdm_engine_honours_an_explicitly_configured_name() -> None:
    stack = _stack(
        databases=DASHBOARD_AND_CDM,
        tools={"oa_cohorts": {"cdm_db": "cdm_reporting"}},
    )

    assert create_cdm_engine(stack) is not None


def test_cdm_engine_reports_a_missing_cdm_database() -> None:
    with pytest.raises(ConfigurationError, match="cdm_db"):
        create_cdm_engine(_stack(databases=DASHBOARD_ONLY))


def test_cdm_field_rejects_a_non_cdm_database() -> None:
    """RefTo(CDMDatabaseConfig) will not accept the generic dashboard entry."""
    stack = _stack(
        databases=DASHBOARD_AND_CDM,
        tools={"oa_cohorts": {"cdm_db": "dashboard_db"}},
    )

    with pytest.raises(ConfigurationError, match="CDMDatabaseConfig"):
        create_cdm_engine(stack)


def test_conventional_fallback_still_requires_a_cdm_database() -> None:
    """The fallback resolves a name no RefTo ever validated.

    With cdm_db unset, create_cdm_engine looks up CONVENTIONAL_CDM_DB directly,
    so resolve_package_config's kind check never sees it. A stack whose 'cdm_db'
    entry is generic would otherwise reach omop-alchemy as the wrong type.
    """
    stack = _stack(
        databases={
            **DASHBOARD_ONLY,
            CONVENTIONAL_CDM_DB: GenericDatabaseConfig(connection="local", schema_name="public"),
        },
    )

    with pytest.raises(ConfigurationError, match="resolves to"):
        create_cdm_engine(stack)


def test_cdm_engine_registers_the_vocabulary_cache_identity(monkeypatch) -> None:
    """Construction must go through omop-alchemy, not resolved.create_engine().

    omop-alchemy's create_cdm_engine also registers the engine's vocabulary
    identity, which is what lets concept-set expansions be shared between
    engines reading the same dataset. Building the engine here instead would
    silently opt every caller out of that cache.
    """
    import omop_alchemy.config as omop_alchemy_config

    seen: list[object] = []
    real = omop_alchemy_config.create_cdm_engine

    def spy(resolved):
        seen.append(resolved)
        return real(resolved)

    monkeypatch.setattr(omop_alchemy_config, "create_cdm_engine", spy)

    engine = create_cdm_engine(_stack(databases=DASHBOARD_AND_CDM))

    assert isinstance(engine, sa.Engine)
    assert len(seen) == 1, "oa-cohorts built the CDM engine itself"
