"""Programmatic Alembic driver for the dashboard schema.

Alembic is configured in code rather than from an ``alembic.ini``, so the
migration directory ships inside the installed wheel and the commands work from
anywhere without a repository checkout.

Nothing here runs implicitly. ``upgrade`` and ``stamp`` are only ever reached
from an explicit ``oa-cohorts schema ...`` command.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

MIGRATIONS_PATH = Path(__file__).resolve().parent / "migrations"


@dataclass(frozen=True)
class RevisionInfo:
    revision: str
    down_revision: str | None
    message: str
    is_head: bool


@dataclass(frozen=True)
class MigrationStatus:
    current: str | None
    head: str | None
    pending: tuple[RevisionInfo, ...]

    @property
    def is_stamped(self) -> bool:
        return self.current is not None

    @property
    def is_up_to_date(self) -> bool:
        return self.current is not None and self.current == self.head


def alembic_config(engine: sa.Engine | None = None, *, url: str | None = None) -> Config:
    """Build an Alembic ``Config`` pointed at the packaged migrations."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_PATH))
    # version_locations is left unset on purpose: Alembic defaults to
    # <script_location>/versions, and setting it explicitly triggers a
    # deprecation warning about path separators for no gain.
    #
    # Alembic wants a URL present for offline mode; online mode uses the engine
    # in attributes and ignores it.
    config.set_main_option("sqlalchemy.url", url or (str(engine.url) if engine else ""))
    if engine is not None:
        config.attributes["engine"] = engine
    return config


def script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(alembic_config())


def head_revision() -> str | None:
    return script_directory().get_current_head()


def current_revision(engine: sa.Engine) -> str | None:
    """Return the revision the database is stamped at, or ``None`` if unstamped."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _revision_info(script: object, head: str | None) -> RevisionInfo:
    down = getattr(script, "down_revision", None)
    return RevisionInfo(
        revision=getattr(script, "revision", ""),
        down_revision=down if isinstance(down, str) or down is None else ", ".join(down),
        message=getattr(script, "doc", "") or "",
        is_head=getattr(script, "revision", None) == head,
    )


def migration_status(engine: sa.Engine) -> MigrationStatus:
    """Return the current revision, the head, and what lies between them."""
    scripts = script_directory()
    head = scripts.get_current_head()
    current = current_revision(engine)

    pending: list[RevisionInfo] = []
    if head is not None and current != head:
        # iterate_revisions walks head -> current; reverse into apply order.
        pending = [_revision_info(script, head) for script in scripts.iterate_revisions(head, current)]
        pending.reverse()

    return MigrationStatus(current=current, head=head, pending=tuple(pending))


def history() -> tuple[RevisionInfo, ...]:
    """Return every revision, oldest first."""
    scripts = script_directory()
    head = scripts.get_current_head()
    revisions = [_revision_info(script, head) for script in scripts.walk_revisions()]
    revisions.reverse()
    return tuple(revisions)


def upgrade(engine: sa.Engine, revision: str = "head") -> None:
    """Apply migrations up to ``revision``. Explicit callers only."""
    command.upgrade(alembic_config(engine), revision)


def downgrade(engine: sa.Engine, revision: str) -> None:
    """Revert migrations down to ``revision``. Explicit callers only."""
    command.downgrade(alembic_config(engine), revision)


def stamp(engine: sa.Engine, revision: str = "head") -> None:
    """Record ``revision`` without running any DDL.

    This is how an existing database adopts the baseline: the schema is already
    there, so it is declared current rather than rebuilt.
    """
    command.stamp(alembic_config(engine), revision)


def upgrade_sql(engine: sa.Engine, revision: str = "head", *, from_revision: str | None = None) -> str:
    """Return the SQL an upgrade would run, without executing anything."""
    config = alembic_config(engine, url=str(engine.url))
    start = from_revision if from_revision is not None else (current_revision(engine) or "base")
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        command.upgrade(config, f"{start}:{revision}", sql=True)
    return buffer.getvalue()


def revise(engine: sa.Engine, message: str, *, autogenerate: bool = True, rev_id: str | None = None) -> None:
    """Author a new revision against ``engine``.

    Autogenerate compares the models to a database, so ``engine`` should point
    at one already upgraded to the current head — typically a scratch database,
    not production.

    Autogenerate does not detect changes to an enum's value set. When a label
    changes, add the ``ALTER TYPE`` to the generated file by hand.
    """
    command.revision(alembic_config(engine), message=message, autogenerate=autogenerate, rev_id=rev_id)


__all__ = [
    "MIGRATIONS_PATH",
    "MigrationStatus",
    "RevisionInfo",
    "alembic_config",
    "current_revision",
    "downgrade",
    "head_revision",
    "history",
    "migration_status",
    "revise",
    "script_directory",
    "stamp",
    "upgrade",
    "upgrade_sql",
]
