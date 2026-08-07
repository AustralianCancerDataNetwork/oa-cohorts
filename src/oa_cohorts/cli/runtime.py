from __future__ import annotations

import os
from typing import NoReturn

import sqlalchemy as sa
import typer
from oa_configurator import ConfigurationError
from rich.console import Console
from sqlalchemy.exc import SQLAlchemyError

from ..config import OaCohortsConfig
from .ui import render_error


def _redacted_engine_url(engine: sa.Engine) -> str:
    return engine.url.render_as_string(hide_password=True)


def resolve_engine(*, database_url: str | None) -> tuple[sa.Engine, str | None]:
    if database_url:
        engine = sa.create_engine(database_url)
        return engine, _redacted_engine_url(engine)

    try:
        engine = OaCohortsConfig.get_engine()
        return engine, None
    except ConfigurationError:
        raise
    except FileNotFoundError as exc:
        env_url = os.getenv("ENGINE")
        if env_url:
            engine = sa.create_engine(env_url)
            return engine, _redacted_engine_url(engine)
        raise FileNotFoundError(
            "No oa-cohorts dashboard database configured. Provide --database-url, "
            "configure oa_cohorts with omop-config, or set ENGINE for a local override."
        ) from exc


def handle_cli_error(console: Console, exc: Exception) -> NoReturn:
    if isinstance(exc, SQLAlchemyError):
        detail = str(exc).strip()
        message = f"Database operation failed: {exc.__class__.__name__}."
        if detail:
            message = f"{message} Detail: {detail}"
        console.print(render_error(message))
        raise typer.Exit(code=1) from exc

    if isinstance(exc, (RuntimeError, ValueError, FileNotFoundError, NotADirectoryError)):
        console.print(render_error(str(exc)))
        raise typer.Exit(code=1) from exc

    console.print(render_error(str(exc)))
    raise typer.Exit(code=1) from exc
