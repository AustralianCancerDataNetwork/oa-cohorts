# OA Cohorts – Reporting & Cohort Execution Engine

This package provides the core machinery for defining, executing, and inspecting cohort-based reports over OMOP-style clinical data. It’s designed to support building real-world evidence reports from composable clinical rules, measures, cohorts, and indicators, with both programmatic APIs and lightweight HTML rendering for debugging and exploration.

The framework implemented here supports configuration-driven clinical quality indicators over OMOP-harmonised data, with explicit support for disease and treatment episodes, temporality, and combinatorial logic. Measures can be defined in terms of diagnoses, treatments, procedures, observations, measurements, and demographics, and composed into clinically interpretable cohorts and indicators. 

This enables the same indicator definitions to support bulk benchmarking, trend analysis over time, and patient-level drill-down, without rewriting query logic for each use case. In practice, this provides a bridge between formal indicator specifications and the operational reality of multidisciplinary care.

At a high level, the system lets you:

* Define query rules (exact, hierarchical, scalar thresholds, phenotypes, etc.)
* Combine rules into subqueries
* Build measures from subqueries (including composite measures with AND/OR/EXCEPT logic)
* Group measures into dash cohorts and cohort definitions
* Define indicators (numerator/denominator pairs)
* Assemble everything into a report
* Execute the report against a database session and materialise results as in-memory member sets
* Inspect SQL, executability, and structure via HTML renderers (handy in notebooks)

This is intentionally object-centric: once a report is executed, downstream payloads are assembled from the resolved cohort and indicator member sets, with report-level demography fetched only for the in-scope cohort person_ids.

## Diagnosis target semantics

The diagnosis layer now distinguishes between two common clinical questions:

* `dx_any`: any diagnosis row attached to the current disease episode
* `dx_primary`: diagnosis rows restricted to the patient's primary diagnosis episode anchor

In practice, `dx_primary` is the safer target when a cohort or indicator should be defined by the diagnosis present at the start of the primary episode of care, rather than by later progression or metastatic episodes that may carry related diagnosis coding.

That distinction matters most for report definitions that need to answer questions like "was this a lung primary cohort?" rather than the broader question "did this episode ever carry a lung-related diagnosis code?"

## What’s here (roughly)

* `Report / ReportCohortMap`: Top-level report definition, linking cohorts and indicators.
* `DashCohort / DashCohortDef`: User-facing cohort groupings backed by executable measures.
* `Measure / MeasureSQLCompiler / MeasureExecutor`: The core executable units. Measures compile to SQL, execute against a session, and materialise member sets with dating and episode context.
* `Indicator`: Numerator/denominator semantics over measures, including optional indicator-level relative date windows anchored to report cohort membership.
* `QueryRule (+ subclasses)`: The rule DSL: exact matches, hierarchies, exclusions, scalar thresholds, phenotypes, substring matches, etc.
* `HTMLRenderable mixins`: Lightweight visualisation of structure, SQL previews, and executability for debugging and exploration.

## Execution model 

```python
report.execute(session)
report.assert_executed()

rows = report.members(executor)    # all cohort members
indicators = report.indicators     # output rows are built per denominator member within the report cohort
```

### Indicator-relative date windows

Indicators can optionally define dynamic numerator and denominator date windows using:

* `numerator_max_days_prior`
* `numerator_max_days_post`
* `denominator_max_days_prior`
* `denominator_max_days_post`

These windows are evaluated relative to the report cohort membership date, not globally on the reusable measure definition. This keeps measures portable while allowing the same measure to participate in different indicators with different timing requirements.

Execution semantics:

* measures still execute broadly and materialise their full `MeasureMember` sets
* indicator row assembly then narrows numerator and denominator rows relative to the in-scope report cohort membership date
* when the denominator is the full report cohort (`measure_id = 0`), filtering is still evaluated per cohort membership row so different in-scope episodes for the same person can qualify differently
* if a window is configured and either the anchor date or candidate member date is missing, that candidate does not satisfy the dated comparison

### Status

This is a working internal engine under active development. APIs may shift.

## Docker

The repo includes a lightweight CLI container under `docker/docker-compose.yaml` that joins the external `cava-network`.

### Databases

oa-cohorts uses two separate databases:

* **`dashboard_db`** — where oa-cohorts stores its report, indicator, and measure configuration. This is the database the CLI commands (`import-config`, `report-summary`, `schema *`, etc.) read from, write to, and migrate. oa-cohorts owns it and provisions it via `omop-config configure oa_cohorts`.
* **`cdm_db`** — the OMOP CDM database used for cohort execution. Owned by `omop_alchemy` and shared, not provisioned by oa-cohorts. It is optional: nothing in the schema, import or summary commands reads the CDM, so a dashboard-only deployment needs no CDM configured at all.

Left unset, `cdm_db` falls back to a database entry literally named `cdm_db` — the same name `omop_alchemy` and `omop_constructs` default their own fields to, which is how the three share one CDM without importing each other's config. Point oa-cohorts at a differently-named entry with `omop-config configure oa_cohorts --cdm-db <name>`.

Both can share the same physical server, or be separate databases in production. To share a server but use different schemas, declare two `[databases.*]` entries over one `[connections.*]` entry:

```toml
[connections.local]
dialect = "postgresql+psycopg"
host = "localhost"
database_name = "mydb"

[databases.dashboard_db]
kind = "generic"
connection = "local"
schema_name = "public"       # schema where oa-cohorts stores its config tables

[databases.cdm_db]
kind = "cdm"
connection = "local"
schema_name = "omop"         # OMOP CDM schema
vocab_schema = "omop"
results_schema = "results"
```

`dashboard_db` is declared `kind = "generic"` and `cdm_db` `kind = "cdm"`; oa-cohorts validates both at resolution time, so pointing `--cdm-db` at a generic entry fails with a clear error rather than at first query.

### Local SQLite bundle

After running `dash_config/export_vocab_subset.py`, the companion importer can build a local SQLite file from the Athena vocabulary files and the exported dashboard configuration:

```bash
python dash_config/import_to_sqlite.py 20260827 --output dash.db
```

Use `--dashboard-only` to omit the CDM vocabulary tables and create a file containing only the dashboard configuration. The importer creates a temporary stack `config.toml` so the logical `dashboard_db` and `cdm_db` resources both resolve to the same SQLite file; the temporary config is removed afterwards.

`--database-url` remains available as a per-command override, and `ENGINE` can be set as a local fallback when no stack config file is present.

Example:

```bash
cd docker
docker compose up -d oa-cohorts
docker compose exec oa-cohorts oa-cohorts --help
docker compose exec oa-cohorts oa-cohorts import-config /app/dash_config
```
