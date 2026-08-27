# Running the test suite

```bash
uv run pytest                 # everything; Postgres tests skip if unconfigured
uv run pytest -m "not requires_database"   # explicitly SQLite-only
```

Nothing needs a database by default. The Postgres tests skip themselves with a
message naming what to configure.

## Test markers

| Marker | Meaning |
|--------|---------|
| *(none)* | Runs on SQLite or in-process, no external dependencies |
| `requires_database("test_dashboard_db")` | Needs a PostgreSQL database configured under that name |

`requires_database` comes from oa-configurator's pytest plugin. It skips when
the named database is absent from your stack config, and **fails** when that
database's connection is not marked `test_only`. These tests run
`DROP SCHEMA public CASCADE` between cases, so that guard is the thing standing
between them and a real dashboard database.

## PostgreSQL integration tests

`tests/schema/test_schema_postgres.py` covers what SQLite cannot: native `ENUM`
types, `ALTER TYPE ... ADD VALUE`, and the `compare_type=True` comparison
against a real backend. Production is Postgres, and both model fixes that
prompted this package were phantom diffs visible only there.

### One-time setup

Two connections to the one container. That is the topology oa-configurator
expects, not a workaround — see below.

```bash
docker compose -f tests/docker-compose.yaml up -d

# admin / production role -> backs dashboard_db
omop-config connections add pg_admin \
  --dialect postgresql+psycopg \
  --host localhost --port 55433 \
  --user test --password test \
  --database-name oa_cohorts_test \
  --test-only false

# disposable test target -> backs test_dashboard_db
omop-config connections add pg_test \
  --dialect postgresql+psycopg \
  --host localhost --port 55433 \
  --user test --password test \
  --database-name oa_cohorts_test_db \
  --test-only true

omop-config databases add dashboard_db \
  --kind generic --connection pg_admin --schema-name public
omop-config databases add test_dashboard_db \
  --kind generic --connection pg_test --schema-name public

omop-config configure oa_cohorts \
  --dashboard-db dashboard_db \
  --test-dashboard-db test_dashboard_db
```

You do not need to create `oa_cohorts_test_db` yourself — `ensure_test_db_exists()`
creates it through `pg_admin`'s credentials on first use.

**Why both connections.** `--test-only true` on `pg_test` is required, not cosmetic:
`OaCohortsConfig.test_dashboard_db` is `RefTo(GenericDatabaseConfig, is_test=True)`, so a
connection without it is refused. And `--test-only false` on `pg_admin` is equally
required: `dashboard_db` carries no `is_test`, so it is refused if it points at a
`test_only` connection. The admin connection is also how the plugin finds credentials to
create the test database — `pytest_plugin._find_admin_url` looks for a non-test-only
connection on the same host, and returns `None` if there isn't one, leaving both
`ensure_test_*` helpers as silent no-ops.

Do not "simplify" this to one connection by flipping a flag; one of the two fields will
stop resolving.

To keep this out of your real `~/.config/omop/config.toml`, point
`OA_CONFIG_PATH` at a scratch file first — it is read once at import, so export
it before running anything:

```bash
export OA_CONFIG_PATH=/tmp/oa-cohorts-test-config.toml
touch "$OA_CONFIG_PATH" && chmod 600 "$OA_CONFIG_PATH"
```

### Running

```bash
uv run pytest -m requires_database -v
docker compose -f tests/docker-compose.yaml down
```

CI runs the same commands against a service container — see `build-test-postgres` in
`.github/workflows/ci.yml`, which carries the full reasoning in comments. `cdm_db` is not
configured there: it is optional by design (a dashboard-only deployment is a real
deployment) and nothing in the suite reads the CDM.
