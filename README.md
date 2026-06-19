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

### Resources

oa-cohorts uses two separate database resources:

* **`dashboard_db`** — where oa-cohorts stores its report, indicator, and measure configuration. This is the database the CLI commands (`import-config`, `report-summary`, etc.) read from and write to. oa-cohorts owns and provisions this resource via `omop-config configure oa_cohorts`.
* **`cdm_db`** — the OMOP CDM database used for cohort execution. Typically shared with `omop_alchemy` or `omop_constructs`; not provisioned by oa-cohorts directly.

Both resources can share the same physical database server, or be separate databases in production. To share a server but use different schemas, declare two `[resources.*]` entries that both point to the same `[databases.*]` entry:

```toml
[databases.mydb]
dialect = "postgresql+psycopg2"
host = "localhost"
database_name = "mydb"

[resources.dashboard_db]
database = "mydb"
cdm_schema = "public"        # schema where oa-cohorts stores its config tables

[resources.cdm_db]
database = "mydb"
cdm_schema = "omop"          # OMOP CDM schema
vocab_schema = "omop"
results_schema = "results"
```

Note: `[resource_aliases]` maps a semantic name to an *existing* resource and inherits its full config (including schema). It is not the right tool for "same server, different schema" — use two separate resource entries as above.

The CLI resolves `dashboard_db` by default (or the `oa_cohorts.default_resource` configured during setup). `--database-url` remains available as a per-command override, and `ENGINE` can be set as a local fallback when no stack config file is present.

Example:

```bash
cd docker
docker compose up -d oa-cohorts
docker compose exec oa-cohorts oa-cohorts --help
docker compose exec oa-cohorts oa-cohorts import-config /app/dash_config
```

When using stack configuration, ensure the `dashboard_db` host is reachable on `cava-network`, for example `postgresql+psycopg2://user:password@postgres:5432/dbname`. For one-off local overrides, pass `--database-url` or set `ENGINE` before invoking the command.
