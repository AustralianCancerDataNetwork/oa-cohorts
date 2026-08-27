"""Import one exported dashboard bundle into a local SQLite database.

Examples
--------

Build a SQLite file containing the dashboard tables and vocabulary::

    python import_to_sqlite.py 20260827 --output dash.db

Build a dashboard-only SQLite file::

    python import_to_sqlite.py 20260827 --output dashboard.db --dashboard-only

"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa

CONFIG_ROOT = Path(__file__).resolve().parent


def _find_bundle(config_dir: Path) -> Path:
    bundles = sorted(
        path for path in config_dir.glob("bundle*.json") if path.is_file()
    )
    if len(bundles) != 1:
        raise ValueError(
            f"Expected exactly one config bundle (bundle*.json) in {config_dir}; "
            f"found {len(bundles)}: {[path.name for path in bundles]}"
        )

    bundle = bundles[0]
    try:
        payload = json.loads(bundle.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read config bundle {bundle}: {exc}") from exc

    scope = payload.get("scope")
    if (
        not isinstance(scope, dict)
        or scope.get("type") != "all"
        or scope.get("library_id") != "ALL"
    ):
        raise ValueError(
            f"Config bundle {bundle} is not an ALL export; expected scope type='all', "
            "library_id='ALL'"
        )
    return bundle


def _find_transport_dir(config_dir: Path) -> Path:
    candidates = (
        config_dir / "transport" / "dash_config",
        config_dir / "transport",
        config_dir,
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(f"Could not find transport CSV directory below {config_dir}")


def _config_string(value: str | Path) -> str:
    """Encode a filesystem value as a TOML basic string."""
    return json.dumps(str(value))


def _stack_config_text(
    database_path: Path,
    *,
    include_cdm: bool,
    vocab_path: Path,
) -> str:
    """Return the minimal oa-configurator stack for this import."""
    lines = [
        "[connections.local]",
        'dialect = "sqlite"',
        f"database_name = {_config_string(database_path)}",
        "",
        "[databases.dashboard_db]",
        'kind = "generic"',
        'connection = "local"',
        "",
        "[tools.oa_cohorts]",
        'dashboard_db = "dashboard_db"',
    ]
    if include_cdm:
        # SQLite has only its built-in ``main`` schema.  The logical CDM,
        # vocabulary, results, and dashboard resources still remain distinct
        # config resources while all physical tables land in this file.
        lines.extend(
            [
                'cdm_db = "cdm_db"',
                "",
                "[databases.cdm_db]",
                'kind = "cdm"',
                'connection = "local"',
                'schema_name = "main"',
                'vocab_schema = "main"',
                'results_schema = "main"',
                "",
                "[tools.omop_alchemy]",
                'cdm_db = "cdm_db"',
                f"athena_source_path = {_config_string(vocab_path)}",
            ]
        )
    return "\n".join(lines) + "\n"


@contextmanager
def _temporary_stack_config(
    database_path: Path,
    *,
    include_cdm: bool,
    vocab_path: Path,
) -> Iterator[Path]:
    """Create and activate the temporary stack config used by the import."""
    with tempfile.TemporaryDirectory(prefix="oa-cohorts-sqlite-") as temp_dir:
        config_path = Path(temp_dir) / "config.toml"
        config_path.write_text(
            _stack_config_text(
                database_path,
                include_cdm=include_cdm,
                vocab_path=vocab_path,
            ),
            encoding="utf-8",
        )
        config_path.chmod(0o600)

        previous_config_path = os.environ.get("OA_CONFIG_PATH")
        os.environ["OA_CONFIG_PATH"] = str(config_path)
        try:
            yield config_path
        finally:
            if previous_config_path is None:
                os.environ.pop("OA_CONFIG_PATH", None)
            else:
                os.environ["OA_CONFIG_PATH"] = previous_config_path


def _resolve_configured_engines(
    *,
    config_path: Path,
    include_cdm: bool,
) -> tuple[sa.Engine, sa.Engine | None]:
    """Resolve the dashboard and optional CDM engines from the temp config.

    The TOML is parsed explicitly rather than relying on the configurator's
    process-global active-config path. The environment variable is still set
    by ``_temporary_stack_config`` for any downstream package code that reads
    the active config convention.
    """
    from oa_configurator import Resolver, StackConfig

    from oa_cohorts.config import OaCohortsConfig, create_cdm_engine

    stack = StackConfig.model_validate(
        tomllib.loads(config_path.read_text(encoding="utf-8"))
    )
    resolver = Resolver(stack)
    package_config = resolver.resolve_package_config(OaCohortsConfig)
    dashboard_engine = resolver.resolve_engine(package_config.dashboard_db)
    if not include_cdm:
        return dashboard_engine, None

    cdm_engine = create_cdm_engine(stack)
    if cdm_engine.dialect.name != "sqlite":
        dashboard_engine.dispose()
        cdm_engine.dispose()
        raise ValueError(
            "The generated import configuration did not resolve cdm_db to SQLite"
        )
    if not isinstance(cdm_engine, sa.Engine):
        dashboard_engine.dispose()
        cdm_engine.dispose()
        raise TypeError("The resolved cdm_db resource did not produce a SQLAlchemy engine")
    return dashboard_engine, cdm_engine


def import_bundle(
    subfolder_name: str,
    output_path: str | Path = "dash.db",
    *,
    include_cdm: bool = True,
    overwrite: bool = False,
) -> dict[str, dict[str, int]]:
    """Import dashboard CSVs and, optionally, Athena vocabularies.

    Parameters
    ----------
    subfolder_name
        Direct child folder below this file's ``dash_config`` directory.
    output_path
        SQLite file to create or update.
    include_cdm
        Load ``vocabs`` and configure ``cdm_db`` when true.  Set false for a
        dashboard-only database.
    overwrite
        Remove an existing output file before importing.  Without this flag,
        an existing file is rejected so a snapshot import cannot silently
        retain rows from an older vocabulary export.
    """
    if (
        Path(subfolder_name).is_absolute()
        or Path(subfolder_name).name != subfolder_name
        or subfolder_name in {"", ".", ".."}
    ):
        raise ValueError(
            "subfolder_name must be the name of a direct child of dash_config"
        )

    config_dir = CONFIG_ROOT / subfolder_name
    if not config_dir.is_dir():
        raise NotADirectoryError(f"Dashboard config subfolder does not exist: {config_dir}")
    _find_bundle(config_dir)
    transport_dir = _find_transport_dir(config_dir)
    vocab_dir = config_dir / "vocabs"
    if include_cdm and not vocab_dir.is_dir():
        raise FileNotFoundError(f"Athena vocabulary directory does not exist: {vocab_dir}")

    database_path = Path(output_path).expanduser().resolve()
    if database_path.exists():
        if not overwrite:
            raise FileExistsError(
                f"Output database already exists: {database_path}; "
                "pass --overwrite to replace it"
            )
        if not database_path.is_file():
            raise IsADirectoryError(f"Output path is not a file: {database_path}")
        database_path.unlink()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with _temporary_stack_config(
        database_path,
        include_cdm=include_cdm,
        vocab_path=vocab_dir,
    ) as config_path:
        dashboard_engine, cdm_engine = _resolve_configured_engines(
            config_path=config_path,
            include_cdm=include_cdm
        )
        try:
            import sqlalchemy.orm as so

            from oa_cohorts.cli.config_import import import_config_directory

            with so.Session(dashboard_engine) as session:
                config_results = import_config_directory(
                    transport_dir,
                    session,
                    create_tables=True,
                )

            counts: dict[str, dict[str, int]] = {
                "dashboard": {
                    result.table_name: result.inserted_rows + result.replaced_rows
                    for result in config_results
                }
            }

            if cdm_engine is not None:
                from omop_alchemy.maintenance.cli_vocab import load_vocab_source

                vocab_report = load_vocab_source(
                    cdm_engine,
                    source_path=vocab_dir,
                    merge_strategy="replace",
                    quote_mode="by_delimiter",
                    chunksize=100_000,
                    bulk_mode=False,
                )
                counts["cdm"] = {
                    result.table_name: result.row_count or 0
                    for result in vocab_report.results
                    if result.row_count is not None
                }
            return counts
        finally:
            dashboard_engine.dispose()
            if cdm_engine is not None:
                cdm_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "subfolder_name",
        help="Direct child folder below dash_config, e.g. 20260827",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dash.db"),
        help="SQLite output path (default: ./dash.db)",
    )
    parser.add_argument(
        "--dashboard-only",
        action="store_true",
        help="Import only transport/dash_config; omit cdm_db and Athena vocabularies",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing SQLite output file",
    )
    args = parser.parse_args()

    try:
        counts = import_bundle(
            args.subfolder_name,
            args.output,
            include_cdm=not args.dashboard_only,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, TypeError, RuntimeError, sa.exc.SQLAlchemyError) as exc:
        parser.error(str(exc))

    for section, section_counts in counts.items():
        print(f"{section}:")
        for table_name, row_count in section_counts.items():
            print(f"  {table_name}: {row_count:,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
