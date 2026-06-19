from __future__ import annotations

import sqlalchemy as sa
import pytest

from oa_configurator import ConfigurationError
from oa_cohorts.config import OaCohortsConfig
from oa_cohorts.cli.runtime import resolve_engine


def test_resolve_engine_uses_explicit_database_url(monkeypatch):
    monkeypatch.delenv("ENGINE", raising=False)

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

    with pytest.raises(FileNotFoundError, match="No oa-cohorts runtime database configured"):
        resolve_engine(database_url=None)


def test_resolve_engine_raises_configuration_error_for_missing_resource(monkeypatch):
    monkeypatch.delenv("ENGINE", raising=False)

    def _raise_key_error(cls, **engine_kwargs):
        raise KeyError("cdm_db")

    monkeypatch.setattr(OaCohortsConfig, "get_engine", classmethod(_raise_key_error))

    with pytest.raises(ConfigurationError, match="cdm_db"):
        resolve_engine(database_url=None)
