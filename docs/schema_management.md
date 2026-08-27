# Schema Management

The dashboard database holds the tables that describe reports, indicators, measures, subqueries, and rules. Those tables are versioned with Alembic, and `oa-cohorts schema` is the interface to them.

## Getting started

The current schema is the baseline. To bring a database under management:

```bash
oa-cohorts schema bootstrap
```

On an existing database this checks that the schema matches the models and then records it as revision `0001_baseline` without running any DDL. If it does not match, bootstrap stops and reports what differs — stamping a mismatched schema as the baseline would make that mismatch permanent and invisible. Pass `--adopt-on-drift` if the difference is known and intentional.

On an empty database the same command creates the tables and records the revision.

## Commands

* `schema check` compares the live schema to the models and lists every difference. Exits non-zero when they disagree, so `oa-cohorts schema check --quiet` works as a deployment gate.
* `schema status` shows the current revision, the head, and anything pending between them.
* `schema bootstrap` creates or adopts, as above.
* `schema upgrade` applies pending migrations.
* `schema sql` prints the SQL an upgrade would run without executing it, for review or for handing to whoever owns the database.
* `schema history` lists the revisions.

## Drift warnings during normal work

Every command that opens the dashboard database checks the schema first, so a mismatch surfaces when you were about to use the data rather than after something has already failed.

Read commands — `report-summary`, `indicator-summary`, `measure-summary` — print a warning and carry on, since their results are still worth seeing with the caveat attached. `import-config` stops instead, because a config import that fails partway through a mismatched schema leaves a partly loaded database that re-running will not fix. Use `--ignore-schema-drift` to import anyway.

If a command fails with a database error regardless, the error is re-checked against the live schema and any mismatch reported as the likely cause, so a bare `no such column: report.report_owner` arrives with an explanation.

## Enum values are not checked

`schema check` uses Alembic's comparison, which inspects column types but never the set of values an existing enum accepts. Adding a member to `RuleTemporality`, or to any other rule enum, produces no revision and no warning — it fails when a row using the new value is written.

Enum changes therefore need their `ALTER TYPE` added to the migration by hand. `sync_enum_labels` does that idempotently:

```python
from oa_cohorts.schema.enum_ops import sync_enum_labels

def upgrade() -> None:
    sync_enum_labels("ruletemporality", (
        "dt_any", "dt_current_start", ..., "dt_referral",
    ))
```

It emits `ALTER TYPE ... ADD VALUE IF NOT EXISTS` for each label, so it adds only what is missing and is safe to re-run. It also does nothing on SQLite, where these columns are plain `VARCHAR`. Pass the full label set rather than just the new one, so the revision stays reproducible.

Because it is idempotent it can sit in a revision that also creates the type — `0001_baseline` does exactly that, as the pattern to copy. Note that `ALTER TYPE ... ADD VALUE` cannot run inside a transaction on Postgres older than 12; the helper raises a clear error rather than failing obscurely.

## Adding a migration

Model changes need a revision. Generate one against a scratch database rather than production:

```python
import sqlalchemy as sa
from oa_cohorts.schema import revise, upgrade

engine = sa.create_engine("sqlite:///scratch.db")
upgrade(engine)                                  # bring it to the current head
revise(engine, "add referral temporality")
```
Do not edit `0001_baseline.py` to track model changes. It is a frozen snapshot of the schema at the point migrations were introduced, and `schema check` reporting clean depends on it staying in step with the models it describes.

## Adopting a database older than the models

`schema bootstrap` stamps head, which asserts the live schema matches the current models. When it does not, the database is not necessarily broken — it may simply match an *earlier* revision, with the difference being migrations that have never been applied.

```bash
oa-cohorts schema bootstrap --revision 0001_baseline
oa-cohorts schema upgrade
```

`bootstrap` refuses to stamp head in that situation rather than claiming migrations ran that never did — which would leave `0002` permanently unapplied and the column types permanently wrong.

`--adopt-on-drift` is the different case — a difference that is known and intentional and that no migration will resolve. It repairs nothing and should not be used to get past an unapplied migration.
