"""Export the vocabularies needed by one ALL dashboard-config bundle.

Usage::

    python export_vocab_subset.py SUBFOLDER SQLALCHEMY_ENGINE

The subfolder is resolved relative to this file's ``dash_config`` directory.
The generated Athena-style vocabulary files are written below
``SUBFOLDER/vocabs``.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Ancestor,
    Concept_Class,
    Concept_Relationship,
    Concept_Synonym,
    Domain,
    Drug_Strength,
    Relationship,
    Source_To_Concept_Map,
    Vocabulary,
)
from omop_semantics.runtime.default_valuesets import runtime

CONFIG_ROOT = Path(__file__).resolve().parent
REQUIRED_VOCABULARY_MODELS = (
    Domain,
    Vocabulary,
    Concept_Class,
    Relationship,
    Concept,
    Concept_Ancestor,
    Concept_Relationship,
    Concept_Synonym,
)
OPTIONAL_VOCABULARY_MODELS = (Drug_Strength, Source_To_Concept_Map)
CHUNK_SIZE = 500
FETCH_SIZE = 10_000


def _chunks(values: Iterable[int], size: int = CHUNK_SIZE) -> Iterator[tuple[int, ...]]:
    chunk: list[int] = []
    for value in values:
        chunk.append(value)
        if len(chunk) == size:
            yield tuple(chunk)
            chunk = []
    if chunk:
        yield tuple(chunk)


def _find_bundle(config_dir: Path) -> Path:
    bundles = sorted(
        path
        for path in config_dir.glob("bundle*.json")
        if path.is_file()
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


def _read_concept_ids(path: Path, column_name: str) -> set[int]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or column_name not in reader.fieldnames:
            raise ValueError(f"{path} does not contain the required column {column_name!r}")

        concept_ids: set[int] = set()
        for row_number, row in enumerate(reader, start=2):
            raw_value = (row.get(column_name) or "").strip()
            if not raw_value:
                continue
            try:
                concept_id = int(raw_value)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid concept ID {raw_value!r} in {path}:{row_number}"
                ) from exc
            # Query rules use OMOP concept_id=0 as the intentional sentinel
            # for presence/absence/scalar rules that have no concept.
            if concept_id != 0:
                concept_ids.add(concept_id)
        return concept_ids


def _config_concept_ids(transport_dir: Path) -> set[int]:
    query_rule_path = transport_dir / "query_rule.csv"
    phenotype_definition_path = transport_dir / "phenotype_definition.csv"
    for path in (query_rule_path, phenotype_definition_path):
        if not path.is_file():
            raise FileNotFoundError(f"Missing required config CSV: {path}")

    return _read_concept_ids(query_rule_path, "concept_id") | _read_concept_ids(
        phenotype_definition_path, "query_concept_id"
    )


def _runtime_concept_ids() -> set[int]:
    """Return all exact and parent concepts declared by the semantics runtime."""
    concept_ids: set[int] = set()
    for valueset_name in dir(runtime):
        if valueset_name.startswith("_"):
            continue
        valueset = getattr(runtime, valueset_name)
        for unit in valueset.members.values():
            concept_ids.update(unit.exact_ids)
            concept_ids.update(unit.parent_ids)
            concept_ids.update(unit.excluded_parent_ids)
    return concept_ids


def _table_columns(table: sa.Table) -> list[sa.Column[Any]]:
    return list(table.columns)


def _selected_vocabulary_ids(
    connection: sa.Connection,
    concept_table: sa.Table,
    required_concept_ids: set[int],
    runtime_concept_ids: set[int],
) -> set[str]:
    concept_ids = required_concept_ids | runtime_concept_ids
    found_ids: set[int] = set()
    vocabulary_ids: set[str] = set()
    for chunk in _chunks(sorted(concept_ids)):
        rows = connection.execute(
            sa.select(concept_table.c.concept_id, concept_table.c.vocabulary_id).where(
                concept_table.c.concept_id.in_(chunk)
            )
        )
        for row in rows:
            found_ids.add(row.concept_id)
            vocabulary_ids.add(row.vocabulary_id)

    missing_required_ids = sorted(required_concept_ids - found_ids)
    if missing_required_ids:
        raise ValueError(
            "The source database is missing represented concept IDs: "
            + ", ".join(str(value) for value in missing_required_ids)
        )
    missing_runtime_ids = sorted(runtime_concept_ids - found_ids)
    if missing_runtime_ids:
        print(
            "Warning: skipping runtime concept IDs not present in the source "
            "vocabulary: "
            + ", ".join(str(value) for value in missing_runtime_ids),
            file=sys.stderr,
        )
    return vocabulary_ids


def _concepts_in_vocabularies(
    concept_table: sa.Table,
    vocabulary_ids: set[str],
) -> sa.ScalarSelect[Any]:
    return sa.select(concept_table.c.concept_id).where(
        concept_table.c.vocabulary_id.in_(sorted(vocabulary_ids))
    ).scalar_subquery()


def _predicate_for_table(
    table_name: str,
    table: sa.Table,
    concept_table: sa.Table,
    vocabulary_ids: set[str],
) -> sa.ColumnElement[bool] | None:
    """Build the Athena vocabulary subset predicate for one table.

    Domain, concept class, and relationship are small reference catalogues
    without a vocabulary ID of their own, so they are emitted in full. The
    remaining relationship tables retain rows touching a concept in one of
    the selected whole vocabularies, including the other endpoint where it is
    outside the selected vocabularies.
    """
    concept_ids = _concepts_in_vocabularies(concept_table, vocabulary_ids)

    if table_name == "concept":
        return table.c.vocabulary_id.in_(sorted(vocabulary_ids))
    if table_name == "vocabulary":
        return table.c.vocabulary_id.in_(sorted(vocabulary_ids))
    if table_name in {"domain", "concept_class", "relationship"}:
        return None
    if table_name == "concept_ancestor":
        return sa.or_(
            table.c.ancestor_concept_id.in_(concept_ids),
            table.c.descendant_concept_id.in_(concept_ids),
        )
    if table_name == "concept_relationship":
        return sa.or_(
            table.c.concept_id_1.in_(concept_ids),
            table.c.concept_id_2.in_(concept_ids),
        )
    if table_name == "concept_synonym":
        return table.c.concept_id.in_(concept_ids)
    if table_name == "drug_strength":
        return sa.or_(
            table.c.drug_concept_id.in_(concept_ids),
            table.c.ingredient_concept_id.in_(concept_ids),
        )
    if table_name == "source_to_concept_map":
        return sa.or_(
            table.c.source_vocabulary_id.in_(sorted(vocabulary_ids)),
            table.c.target_vocabulary_id.in_(sorted(vocabulary_ids)),
            table.c.source_concept_id.in_(concept_ids),
            table.c.target_concept_id.in_(concept_ids),
        )
    raise ValueError(f"Unsupported vocabulary table {table_name!r}")


def _serialise(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _write_table(
    connection: sa.Connection,
    table: sa.Table,
    predicate: sa.ColumnElement[bool] | None,
    output_path: Path,
) -> int:
    statement = sa.select(*_table_columns(table))
    if predicate is not None:
        statement = statement.where(predicate)
    primary_key = list(table.primary_key.columns)
    if primary_key:
        statement = statement.order_by(*primary_key)

    row_count = 0
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        columns = _table_columns(table)
        writer.writerow([column.name for column in columns])
        result = connection.execution_options(stream_results=True).execute(statement)
        while rows := result.fetchmany(FETCH_SIZE):
            for row in rows:
                writer.writerow([_serialise(value) for value in row])
                row_count += 1
    return row_count


def export_vocab_subset(subfolder_name: str, engine_string: str) -> dict[str, int]:
    """Export the selected vocabulary subset and return table row counts."""
    subfolder = Path(subfolder_name)
    if (
        subfolder.is_absolute()
        or subfolder.name != subfolder_name
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

    config_concept_ids = _config_concept_ids(transport_dir)
    runtime_concept_ids = _runtime_concept_ids()
    if not config_concept_ids and not runtime_concept_ids:
        raise ValueError("No represented or runtime concept IDs were found")

    engine = sa.create_engine(engine_string)
    output_dir = config_dir / "vocabs"
    output_dir.mkdir(parents=True, exist_ok=True)

    models = REQUIRED_VOCABULARY_MODELS + OPTIONAL_VOCABULARY_MODELS
    row_counts: dict[str, int] = {}
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        missing_required = [
            model.__tablename__
            for model in REQUIRED_VOCABULARY_MODELS
            if not inspector.has_table(model.__tablename__)
        ]
        if missing_required:
            raise ValueError(
                "The source database is missing required vocabulary table(s): "
                + ", ".join(missing_required)
            )

        concept_table = Concept.__table__
        vocabulary_ids = _selected_vocabulary_ids(
            connection,
            concept_table,
            config_concept_ids,
            runtime_concept_ids,
        )
        print(
            f"Selected {len(vocabulary_ids)} vocabulary/vocabularies: "
            + ", ".join(sorted(vocabulary_ids)),
            file=sys.stderr,
        )

        for model in models:
            table = model.__table__
            if not inspector.has_table(model.__tablename__):
                continue
            predicate = _predicate_for_table(
                model.__tablename__, table, concept_table, vocabulary_ids
            )
            output_path = output_dir / f"{model.__tablename__.upper()}.csv"
            row_counts[model.__tablename__] = _write_table(
                connection, table, predicate, output_path
            )
            print(
                f"Wrote {row_counts[model.__tablename__]:,} rows to {output_path}",
                file=sys.stderr,
            )
    engine.dispose()
    return row_counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subfolder_name", help="Direct child folder below dash_config")
    parser.add_argument("engine_string", help="SQLAlchemy engine URL")
    args = parser.parse_args()
    try:
        export_vocab_subset(args.subfolder_name, args.engine_string)
    except (OSError, ValueError, sa.exc.SQLAlchemyError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
