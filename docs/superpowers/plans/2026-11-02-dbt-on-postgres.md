# dbt on Postgres Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `prumi serve` runs a dbt project built on Postgres with the same startup checks and guarantees as DuckDB, proven by the same tests giving the same numbers on both warehouses in CI.

**Architecture:** A second connection type, `postgres`, next to `duckdb` in `prumi_adapter_dbt/connections/`, with MetricFlow's Postgres renderer in the compile-only client. The demo dbt project builds on either warehouse from the same seeded rows. The adapter tests run against a `warehouse` fixture parametrized over both, and a new test compares the two directly. CI gets a Postgres service container.

**Tech Stack:** Python 3.12, psycopg 3 (`psycopg[binary]`), MetricFlow 0.213.0 (`PostgresSQLSqlPlanRenderer`), dbt-core 1.12.6 with dbt-postgres 1.11.0 (demo only), `postgres:18` image, pytest, GitHub Actions service containers.

**Spec:** `docs/superpowers/specs/2026-11-02-dbt-on-postgres-design.md` (read it before starting any task).

## Global Constraints

- A password, or a connection string holding one, never appears in a log record, an error message, an MCP result, a page, or an exception chained onto an `AdapterStartupError` (raise with `from None`).
- Messages about a failed connection are Prumi's own text built only from `host`, `port`, `dbname`, `user`, `sslmode`; never interpolate the driver's message, `str(exc)` or a conninfo string.
- Database errors at query time keep today's rule: only the exception's type name leaves the adapter.
- Prumi never adjusts, casts or re-aggregates a number on Postgres; numbers come from MetricFlow's SQL.
- Every Postgres connection is read-only at the session level (`default_transaction_read_only = on`).
- `prumi.toml` never takes a password; libpq finds it (`PGPASSWORD`, `~/.pgpass`, service file).
- `[source.dbt.connection]` keys: `duckdb` → `type`, `path`; `postgres` → `type`, `host`, `dbname`, `user` (required), `port` (int, default 5432), `sslmode` (one of `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`), `timezone` (optional). Anything else is refused by name, never echoing a value.
- `connect_timeout` is 10 seconds.
- MetricFlow stays pinned at `0.213.0`. Core never imports an adapter. Code never cites the spec or plan.
- Branch commits: short imperative subject, `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer. Docs change in the same commit as the code they describe.

## Review Focus

1. **Postgres goes away after startup** (restarted, password rotated): a query fails with Prumi's own message, the server keeps running, and no password or driver text reaches logs. Test in Task 4 (`test_a_failed_connection_at_query_time_is_clean`).
2. **`sslmode = "require"` against a server without TLS** (the default `postgres:18` container): startup stops with a message naming `sslmode`, not a traceback. Test in Task 4 (`test_sslmode_the_server_cannot_honor`).
3. **The password comes from `~/.pgpass`, with `PGPASSWORD` unset**: startup works. Test in Task 4 (`test_the_password_can_come_from_a_pgpass_file`).
4. **Filter values with a quote (`Côte d'Ivoire`) and NULL regions on Postgres** give the same rows as DuckDB. Test in Task 6 (in `QUERIES`).
5. **The role's own time zone differs from UTC and `timezone` is unset**: Prumi uses the role's time zone, not UTC or the machine's. Test in Task 4 (`test_timezone_decides_the_day_of_a_timestamptz`).

---

### Task 1: Connection settings refuse unknown keys

`docs/configuration.md` promises that an unknown key stops the server, but `[source.dbt.connection]` silently ignores everything except `type` and `path`. Make the DuckDB connection refuse unknown keys, and give each connection type its own hint for the missing-tables message, so Postgres can have its own in Task 4.

**Files:**
- Modify: `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/connections/__init__.py`
- Modify: `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/connections/duckdb.py`
- Modify: `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/adapter.py:72-88`
- Test: `packages/prumi-adapter-dbt/tests/test_startup.py`

**Interfaces:**
- Produces: `refuse_unknown_keys(config: Mapping[str, Any], allowed: Collection[str], connection_type: str) -> None` in `connections/__init__.py`. It raises `AdapterStartupError` naming the unknown keys, sorted, and the allowed ones, never the values.
- Produces: the `Connection` protocol gains `missing_hint: str`, a sentence appended to the missing-tables message. DuckDB's hint: `"For DuckDB the database name is the file name without its extension."`

- [ ] **Step 1: Write the failing test** in `test_startup.py`:

```python
def test_unknown_connection_key_is_refused_without_its_value(config, tmp_path):
    config["connection"]["passwd"] = "SENTINEL-7f3a9c"
    message = start(config, tmp_path)
    assert "passwd" in message and "[source.dbt.connection]" in message
    assert "SENTINEL" not in message
```

- [ ] **Step 2: Run it.** `uv run pytest packages/prumi-adapter-dbt/tests/test_startup.py -k unknown_connection_key -v`. Expected: FAIL (`DID NOT RAISE`).
- [ ] **Step 3: Implement** `refuse_unknown_keys` and call it from `DuckDBConnection.from_config` with `{"type", "path"}`. Add `missing_hint` to the protocol and `DuckDBConnection`. In `adapter.py`, end the missing-tables message with `connection.missing_hint` instead of the DuckDB sentence. Keep `test_renamed_duckdb_file`'s assertions passing.
- [ ] **Step 4: Run** `uv run pytest packages/prumi-adapter-dbt/tests/test_startup.py -v`. Expected: all pass.
- [ ] **Step 5: Update docs.** `docs/configuration.md` § `[source.dbt.connection]`: no change in wording is needed if it already says unknown keys stop the server; check it does.
- [ ] **Step 6: Commit:** `git commit -m "Refuse unknown keys in the dbt connection settings"`.

---

### Task 2: The demo builds on Postgres

**Files:**
- Modify: `examples/demo-dbt-project/profiles.yml`, `models/metricflow_time_spine.sql`, `generate.py`, `build.py`, `pyproject.toml` (+ its `uv.lock`), `README.md` ("How it's built")
- Modify: `conftest.py` (repo root), `pyproject.toml` (root: the `postgres` marker), `.gitignore` (`target-postgres/`)
- Modify: `CONTRIBUTING.md` (running the Postgres tests)
- Test: `examples/demo-dbt-project/tests/test_build.py`

**Interfaces:**
- Produces, in `generate.py`: `generate_rows(seed: int = SEED) -> dict[str, list[tuple]]`, `generate(db_path: Path, seed: int = SEED) -> dict[str, int]` (unchanged behavior) and `generate_postgres(conninfo: str, seed: int = SEED) -> dict[str, int]`. The last one drops and recreates schema `raw`, creates the `TABLES` and loads the rows with `COPY ... FROM STDIN`.
- Produces, in `build.py`: `build(target: str = "duckdb") -> None` and a `--target {duckdb,postgres}` flag. The Postgres build reads `DEMO_POSTGRES_HOST` (default `localhost`), `DEMO_POSTGRES_PORT` (`5432`), `DEMO_POSTGRES_USER` (`postgres`), `DEMO_POSTGRES_PASSWORD` (required) and `DEMO_POSTGRES_DBNAME` (`prumi_demo`). It creates the database if it's missing, drops schemas `raw` and `main` with `CASCADE`, generates, runs `dbt build` and `dbt parse --target postgres` into a temporary target path, then replaces `examples/demo-dbt-project/target-postgres/`. The DuckDB outputs are untouched.
- Produces, in the root `conftest.py`:
  - `PostgresServer(host: str, port: int, admin_user: str, admin_password: str)`, from `PRUMI_TEST_POSTGRES_HOST` (presence enables Postgres tests), `PRUMI_TEST_POSTGRES_PORT` (default `5432`), `PRUMI_TEST_POSTGRES_USER` (`postgres`), `PRUMI_TEST_POSTGRES_PASSWORD` (`postgres`).
  - `PostgresDemo(server: PostgresServer, dbname: str = "prumi_demo", reader: str = "prumi_reader", reader_password: str = "prumi-reader-test", manifest_path: Path)` with `connection_config(**overrides) -> dict`, the `[source.dbt.connection]` table for the reader, and `admin_connect(dbname: str | None = None) -> psycopg.Connection`.
  - A session fixture `postgres_demo() -> PostgresDemo`. It skips when `PRUMI_TEST_POSTGRES_HOST` is unset, and fails instead when `PRUMI_REQUIRE_POSTGRES` is set. It builds the Postgres demo when `target-postgres/semantic_manifest.json` is stale against `_demo_inputs()` or `prumi_demo.main.orders` doesn't exist. It (re)creates role `prumi_reader` with `LOGIN PASSWORD 'prumi-reader-test'`, grants `CONNECT` on the database, `USAGE` on schema `main` and `SELECT` on all its tables, and sets `os.environ["PGPASSWORD"]` to the reader password for the session (restoring it afterwards).
  - Every test using `postgres_demo` gets the `postgres` marker (as `demo` is added today).

- [ ] **Step 1: Write the failing tests** in `examples/demo-dbt-project/tests/test_build.py`:

```python
def test_time_spine_covers_2023_to_2027(demo_project):
    with duckdb.connect(str(demo_project.db_path), read_only=True) as conn:
        assert conn.execute(
            "SELECT count(*), min(date_day), max(date_day) FROM main.metricflow_time_spine"
        ).fetchone() == (1826, dt.date(2023, 1, 1), dt.date(2027, 12, 31))


def test_postgres_build_has_the_same_rows_as_duckdb(demo_project, postgres_demo):
    manifest = json.loads(postgres_demo.manifest_path.read_text())
    relations = {
        m["name"]: m["node_relation"]["relation_name"] for m in manifest["semantic_models"]
    }
    assert relations == {name: f'"prumi_demo"."main"."{name}"' for name in MODELS}
    with (
        duckdb.connect(str(demo_project.db_path), read_only=True) as duck,
        postgres_demo.admin_connect() as pg,
    ):
        for name in [*MODELS, "metricflow_time_spine"]:
            sql = f"SELECT count(*) FROM main.{name}"
            assert pg.execute(sql).fetchone()[0] == duck.execute(sql).fetchone()[0], name
```

- [ ] **Step 2: Run them.** `uv run pytest examples/demo-dbt-project/tests/test_build.py -v` with a local Postgres (`docker run -d --name prumi-postgres -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:18` and `PRUMI_TEST_POSTGRES_HOST=localhost`). Expected: the spine test passes on the current build (record its numbers if they differ from the assertion, and fix the assertion to today's DuckDB values, because they must not change); the Postgres test errors (no fixture).
- [ ] **Step 3: Implement.** Add `dbt-postgres==1.11.0` and `psycopg[binary]` to the demo's dependencies. Add the `postgres` output to `profiles.yml` (keep `target: dev` DuckDB as the default), with `schema: main` and the password from `env_var('DEMO_POSTGRES_PASSWORD')`, so relations are `"prumi_demo"."main"."<model>"` and the tests' `main.<table>` SQL works on both. Rewrite the time spine with `dbt.date_spine("day", "cast('2023-01-01' as date)", "cast('2028-01-01' as date)")`, casting `date_day` to `date`. If a `TABLES` type isn't valid on Postgres, use one both accept. Then the root `conftest.py` additions, the `postgres` marker, and the `.gitignore` line.
- [ ] **Step 4: Run** `uv run pytest examples/demo-dbt-project/tests -v`, with and without `PRUMI_TEST_POSTGRES_HOST`. Expected: all pass with it, and the Postgres test is skipped without it. Then `PRUMI_REQUIRE_POSTGRES=1` without the host: expected FAIL with a message saying how to start a Postgres.
- [ ] **Step 5: Docs.** `examples/demo-dbt-project/README.md` "How it's built": the `--target postgres` build, its env vars and `target-postgres/`. The walkthrough stays as it is. In `CONTRIBUTING.md`, add a subsection under "Set up and run the tests" with the `docker run` line, `PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest`, and a note that the Postgres tests skip without it.
- [ ] **Step 6: Commit:** `git commit -m "Build the demo dbt project on Postgres too"`.

---

### Task 3: Postgres settings and dialect

Everything that works without a database: the dialect, the settings, the extras.

**Files:**
- Modify: `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/_metricflow.py:35-37` (`"postgres": (SqlEngine.POSTGRES, PostgresSQLSqlPlanRenderer)`)
- Create: `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/connections/postgres.py`
- Modify: `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/connections/__init__.py` (`open_connection` dispatches `postgres`)
- Modify: `packages/prumi-adapter-dbt/pyproject.toml` (`postgres = ["psycopg[binary]"]`), `packages/prumi-core/pyproject.toml` (`dbt = ["prumi-adapter-dbt[duckdb,postgres]~=0.1.1"]`, keeping the `x-release-please-version` comment), root `pyproject.toml` dev group (`prumi-adapter-dbt[duckdb,postgres]`), `uv.lock`
- Modify: `packages/prumi-core/tests/test_release.py:63,129,221` (the extras assertions)
- Test: `packages/prumi-adapter-dbt/tests/test_postgres_settings.py`, `test_metricflow.py`

**Interfaces:**
- Consumes: `refuse_unknown_keys` (Task 1).
- Produces, in `connections/postgres.py`:
  - `PostgresSettings(host: str, port: int, dbname: str, user: str, sslmode: str | None, timezone: str | None)`, a frozen dataclass, with `from_config(config: Mapping[str, Any]) -> PostgresSettings` (raises `AdapterStartupError`) and `connect_kwargs() -> dict[str, Any]`. The kwargs hold the five settings, `connect_timeout=10`, and session options for read-only and, when set, the time zone. They never hold a password.
  - The module imports psycopg like `duckdb.py` imports duckdb: a missing package raises `AdapterStartupError("The postgres connection needs the psycopg package: install prumi-adapter-dbt[postgres].")`.

- [ ] **Step 1: Write the failing tests** in `test_postgres_settings.py`:

```python
BASE = {"type": "postgres", "host": "db.internal", "dbname": "analytics", "user": "prumi_reader"}


def test_defaults():
    s = PostgresSettings.from_config(BASE)
    assert (s.port, s.sslmode, s.timezone) == (5432, None, None)
    kwargs = s.connect_kwargs()
    assert kwargs["connect_timeout"] == 10 and "password" not in kwargs


def test_password_key_is_refused_without_its_value():
    with pytest.raises(AdapterStartupError) as e:
        PostgresSettings.from_config({**BASE, "password": "SENTINEL-7f3a9c"})
    assert "PGPASSWORD" in str(e.value) and "pgpass" in str(e.value)
    assert "SENTINEL" not in str(e.value) and e.value.__cause__ is None


@pytest.mark.parametrize("missing", ["host", "dbname", "user"])
def test_required_settings(missing): ...  # message names `missing`


@pytest.mark.parametrize(
    "change", [{"port": "5432"}, {"sslmode": "always"}, {"timezone": 3}, {"schema": "x"}]
)
def test_bad_settings_are_refused_by_name(change): ...  # message names the key, not the value


def test_sessions_are_read_only():
    options = PostgresSettings.from_config(BASE).connect_kwargs()["options"]
    assert "default_transaction_read_only=on" in options
```

and in `test_metricflow.py`, `test_postgres_has_a_dialect`: `CompileOnlySqlClient("postgres").sql_engine_type is SqlEngine.POSTGRES`. Update the existing `'oracle'.*duckdb` match to expect both types listed.

- [ ] **Step 2: Run** `uv run pytest packages/prumi-adapter-dbt/tests/test_postgres_settings.py packages/prumi-adapter-dbt/tests/test_metricflow.py -v`. Expected: FAIL (import error).
- [ ] **Step 3: Implement** the dialect entry, `PostgresSettings`, the extras and the lockfile (`uv lock`). Pass the time zone either in `options` (`-c TimeZone=...`, escaping per libpq rules) or with `SET TIME ZONE` after connecting in Task 4. If you choose the latter, `connect_kwargs` carries only read-only and Task 4's test covers the time zone.
- [ ] **Step 4: Run** `uv run pytest packages/prumi-adapter-dbt packages/prumi-core/tests/test_release.py -v`. Expected: all pass (update the three extras assertions to `prumi-adapter-dbt[duckdb,postgres]` and add `Requires-Dist: psycopg[binary]; extra == 'postgres'`).
- [ ] **Step 5: Docs.** `docs/configuration.md` `[source.dbt.connection]`: a table of the `postgres` keys, with the password paragraph (libpq: `PGPASSWORD`, `~/.pgpass`, service file; never in `prumi.toml`), the read-only session, and `timezone`. `packages/prumi-adapter-dbt/README.md`: the `[postgres]` extra and that `prumi[dbt]` installs both drivers.
- [ ] **Step 6: Commit:** `git commit -m "Add Postgres connection settings and MetricFlow's Postgres dialect"`.

---

### Task 4: Connecting to Postgres

**Files:**
- Modify: `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/connections/postgres.py`
- Test: `packages/prumi-adapter-dbt/tests/test_postgres_connection.py` (all tests use `postgres_demo`)
- Modify: `README.md` (Troubleshooting: the new startup messages)

**Interfaces:**
- Consumes: `PostgresSettings` (Task 3), `PostgresDemo` (Task 2), `missing_hint` (Task 1).
- Produces: `PostgresConnection` with `identity_mode = "shared_account"`, `description = f"the Postgres database {dbname} on {host}:{port}"`, `missing_hint`, `from_config(config, config_dir) -> PostgresConnection`, `query(sql, viewer)`, and `missing_relations(relations)`. `from_config` connects once and runs the time-zone check, so a bad connection stops startup.
- Produces: `connect_error_message(exc: psycopg.OperationalError, settings: PostgresSettings) -> str`, used at startup and at query time. It's classified by `exc.sqlstate`:

| sqlstate | Message says |
|---|---|
| `28P01`, `28000` | Postgres refused `user`'s login to `dbname` on `host:port`: check `user`, and the password in `PGPASSWORD` or `~/.pgpass` (Prumi never reads one from `prumi.toml`). |
| `3D000` | No database `dbname` on `host:port`: check `dbname`. |
| `22023` (on the time zone) | `timezone` under `[source.dbt.connection]`: `'<name>'` isn't a time zone Postgres knows. |
| anything else, or `None` | Prumi can't connect to Postgres at `host:port` as `user`: check `host`, `port` and `sslmode`, that the server accepts connections from this machine, and the password's source. |

At query time, a connect failure raises `ConnectionFailed(Exception)`, a new class in `connections/__init__.py` (not an `AdapterStartupError`: the server is already running), with the same message, `from None`.

- `missing_relations` parses each `"db"."schema"."table"`. If any relation's database isn't `dbname`, it raises `AdapterStartupError`: the manifest was parsed against database `<db>`, but the connection's `dbname` is `<dbname>`, and Postgres can't query across databases. A relation the role can't read (`42501`) is collected and raised as one `AdapterStartupError` listing them, with "grant SELECT on these tables to `<user>`". Undefined tables and schemas are returned as missing.

- [ ] **Step 1: Write the failing tests.** `SENTINEL = "SENTINEL-7f3a9c"`. Each test checks the message, and for credentials also `caplog` at DEBUG (as `all_text` in `prumi-core/tests/test_logging.py`) and `error.__cause__ is None`:
  - `test_the_demo_starts_on_postgres`: `DbtAdapter.from_config({"semantic_manifest": str(postgres_demo.manifest_path), "connection": postgres_demo.connection_config()}, tmp_path)` succeeds.
  - `test_wrong_password_never_shows_it`: `monkeypatch.setenv("PGPASSWORD", SENTINEL)`; the message names the user and `PGPASSWORD`; the sentinel is in neither the message nor the logs.
  - `test_unknown_database`, `test_unreachable_port` (port `1`), `test_unknown_time_zone` (`timezone="Mars/Olympus"`): each names its setting.
  - `test_sslmode_the_server_cannot_honor`: `sslmode="require"` against the container (no TLS); the message names `sslmode`.
  - `test_the_password_can_come_from_a_pgpass_file`: `monkeypatch.delenv("PGPASSWORD")`, write `host:port:prumi_demo:prumi_reader:prumi-reader-test` to a `0600` file, set `PGPASSFILE`; the adapter starts.
  - `test_prumi_cannot_write_even_as_a_writer`: connect as the admin user (password via `PGPASSWORD`); `connection.query("CREATE TABLE main.x (a int)", LOCAL_VIEWER)` raises `psycopg.errors.ReadOnlySqlTransaction`.
  - `test_database_mismatch_is_named`: an admin-created empty database `prumi_other`, granted to the reader; the message names both `prumi_demo` and `prumi_other`.
  - `test_unreadable_table_is_named`: `REVOKE SELECT ON main.orders FROM prumi_reader` (re-grant in teardown); the message lists `"prumi_demo"."main"."orders"` and says to grant `SELECT`.
  - `test_a_failed_connection_at_query_time_is_clean`: start the adapter, then `monkeypatch.setenv("PGPASSWORD", SENTINEL)`, then run `Source.execute` (as the server does) with `caplog` at DEBUG. It raises `ConnectionFailed`; neither the logs nor `str(error)` contain the sentinel.
  - `test_timezone_decides_the_day_of_a_timestamptz`: as admin, create schema `tz_check` with table `events(at timestamptz)` holding `'2026-10-14 23:30:00+00'`, and role `prumi_tz_reader` with `ALTER ROLE ... SET timezone = 'Pacific/Kiritimati'` and `SELECT` granted. Query `SELECT date_trunc('day', at)::date FROM tz_check.events` through `PostgresConnection`. With no `timezone` it returns `2026-10-15` (the role's own). With `timezone="UTC"` it returns `2026-10-14`.
- [ ] **Step 2: Run** `PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest packages/prumi-adapter-dbt/tests/test_postgres_connection.py -v`. Expected: FAIL (`PostgresConnection` doesn't exist).
- [ ] **Step 3: Implement** `PostgresConnection` and `connect_error_message`. Open a connection per query and close it, like DuckDB. Every psycopg `OperationalError` raised by `connect` goes through `connect_error_message` and is re-raised `from None`.
- [ ] **Step 4: Run** the same command. Expected: all pass. Then `uv run pytest packages/prumi-adapter-dbt -v`. Expected: all pass.
- [ ] **Step 5: Docs.** In `README.md` Troubleshooting, the startup bullet gains: Prumi can't connect to Postgres or log in, a database mismatch, and a table the role can't read.
- [ ] **Step 6: Commit:** `git commit -m "Connect to Postgres read-only, with startup checks that never show the password"`.

---

### Task 5: The adapter tests run on both warehouses

**Files:**
- Modify: `packages/prumi-adapter-dbt/tests/conftest.py`
- Modify: `test_execute.py`, `test_filters.py`, `test_contract.py`, `test_catalog.py` (only where they assume DuckDB)

**Interfaces:**
- Consumes: `postgres_demo` (Task 2), `PostgresConnection` (Task 4).
- Produces, in the adapter tests' `conftest.py`:
  - `Warehouse(name: str, dbt_config: dict, manifest_path: Path)`, where `name` is `"duckdb"` or `"postgres"`, with `hand(sql: str) -> list[tuple]`. It runs read-only (Postgres as `prumi_reader`) and turns `Decimal` into `float`.
  - A session fixture `warehouse`, parametrized `["duckdb", pytest.param("postgres", marks=pytest.mark.postgres)]`. The Postgres param gets `postgres_demo` through `request.getfixturevalue`.
  - The `adapter` and `compiler` fixtures build from `warehouse` instead of the demo's `prumi.toml`.

- [ ] **Step 1: Rewrite the fixtures** and make every `hand(demo_project, sql)` call `warehouse.hand(sql)`. `test_contract.py`'s module fixtures `mf` and `conn` use `warehouse` too. Make the hand-written SQL portable: `::double` → `::double precision`, and `isodow(x)` → `extract(isodow from x)`. `test_describe_names_the_adapter_and_the_connection_type` expects `connection=warehouse.name`. Tests that are about the DuckDB file itself (renamed file, empty file) keep using `demo_project` and run once.
- [ ] **Step 2: Run** `uv run pytest packages/prumi-adapter-dbt -v` without Postgres. Expected: the DuckDB params pass and the Postgres params skip. If any DuckDB number changed, the SQL rewrite was wrong.
- [ ] **Step 3: Run** `PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest packages/prumi-adapter-dbt -v`. Expected: all pass. If a Postgres number differs, follow the spec ("When they differ"): fix the demo's models or the test's portable SQL, never Prumi. If MetricFlow's Postgres SQL or Postgres itself is the cause, stop and report to the maintainer with the query and both results.
- [ ] **Step 4: Commit:** `git commit -m "Run the adapter tests on DuckDB and Postgres"`.

---

### Task 6: The warehouses agree

**Files:**
- Create: `packages/prumi-adapter-dbt/tests/test_warehouses_agree.py`

**Interfaces:**
- Consumes: `demo_project`, `postgres_demo`, `DbtAdapter`.

- [ ] **Step 1: Write the test.** Build three adapters: DuckDB, Postgres, and Postgres with `timezone="Pacific/Kiritimati"`. `QUERIES` is generated from the DuckDB catalog plus fixed cases:
  - every metric with no grain over `2025-07-01..2026-09-30`, and at each time grain the catalog lists for it;
  - `revenue` by `customer__region`;
  - `revenue` filtered on `customer__country` `eq "Côte d'Ivoire"`;
  - `revenue` by region filtered `customer__region in [None, "Western Europe"]`;
  - the "data through" query (`time_grain="day"`, `order_by` `metric_time` descending, `limit=1`);
  - the distinct values of `customer__region` (`metrics=()`).

```python
@pytest.mark.parametrize("query", QUERIES, ids=query_id)
def test_postgres_gives_duckdb_s_numbers(duck, postgres, postgres_far_from_utc, query):
    expected = normalized(duck.execute(query, LOCAL_VIEWER))
    for adapter in (postgres, postgres_far_from_utc):
        assert normalized(adapter.execute(query, LOCAL_VIEWER)) == expected
```

`normalized(result)` returns `(columns, sorted rows)`. Floats are rounded to 6 places, as `test_contract.normalize` does, and each value is paired with its Python type name, so `int` against `float` is a difference.
- [ ] **Step 2: Run** `PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest packages/prumi-adapter-dbt/tests/test_warehouses_agree.py -v`. Expected: all pass. A failure is a finding: handle it as in Task 5, Step 3. A type-only difference (same value, `int` against `float`) is reported to the maintainer too, not fixed silently.
- [ ] **Step 3: Commit:** `git commit -m "Check that Postgres gives the same numbers as DuckDB"`.

---

### Task 7: The server on the Postgres demo

**Files:**
- Modify: `packages/prumi-core/tests/conftest.py` (add `write_postgres_demo_config(directory: Path, postgres_demo, **connection) -> Path`, the demo config of `write_demo_config` with the Postgres manifest and connection)
- Create: `packages/prumi-core/tests/test_postgres_demo.py`

**Interfaces:**
- Consumes: `postgres_demo` (Task 2), `SuiteServices`, `demo_dashboard`, `prumi.cli.main`.

- [ ] **Step 1: Write the tests:**
  - `test_the_demo_dashboard_publishes_on_postgres` (browser, with the existing skip pattern): `SuiteServices(load_config(path)).publish(*demo_dashboard)` gives `report.ok`, with no issues.
  - `test_the_home_page_shows_data_through_on_postgres`: `TestClient(create_app(services)).get("/")` contains `data through 2026-10-14`.
  - `test_serve_with_a_wrong_password_never_shows_it`: `monkeypatch.setenv("PGPASSWORD", SENTINEL)`; `main(["serve", "--config", str(path)])` returns `REFUSED`. `capsys` stderr starts with `prumi:` and names `PGPASSWORD`, and neither stderr nor `caplog` at DEBUG contains the sentinel.
  - `test_a_password_in_prumi_toml_is_refused`: needs no database. Write the config by hand with the DuckDB demo's manifest (`demo_project.manifest_path`; MetricFlow parses it for any dialect) and a `postgres` connection to `127.0.0.1` port `1` with `password = "SENTINEL-7f3a9c"`. The settings are refused before any connection is attempted, so apply the same checks as above.
- [ ] **Step 2: Run** `PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest packages/prumi-core/tests/test_postgres_demo.py -v`. Expected: all pass (the code exists already; a failure here is a bug to fix in Tasks 3–4's code).
- [ ] **Step 3: Commit:** `git commit -m "Check the server end to end on the Postgres demo"`.

---

### Task 8: CI runs the Postgres tests

**Files:**
- Modify: `.github/workflows/ci.yml` (job `python`), `.github/workflows/release.yml` (job `checks`)
- Modify: `packages/prumi-core/tests/test_release.py`

- [ ] **Step 1: Write the failing test** in `test_release.py`:

```python
@pytest.mark.parametrize(("workflow", "job"), [("ci.yml", "python"), ("release.yml", "checks")])
def test_the_full_suite_runs_on_postgres_in_ci_and_before_a_release(workflow, job):
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / workflow).read_text())["jobs"]
    assert jobs[job]["services"]["postgres"]["image"] == "postgres:18"
    step = next(s for s in jobs[job]["steps"] if s.get("run") == "uv run pytest")
    assert step["env"]["PRUMI_REQUIRE_POSTGRES"] == "1"
    assert step["env"]["PRUMI_TEST_POSTGRES_HOST"] == "localhost"
```

- [ ] **Step 2: Run it.** Expected: FAIL (`KeyError: 'services'`).
- [ ] **Step 3: Add the service** to both jobs: image `postgres:18`, `POSTGRES_PASSWORD: postgres`, port `5432:5432`, and a `pg_isready` health check. Add the two env vars to the `uv run pytest` step next to `PRUMI_REQUIRE_BROWSER`.
- [ ] **Step 4: Run** `uv run pytest packages/prumi-core/tests/test_release.py -v`. Expected: all pass.
- [ ] **Step 5: Commit**, push the branch, and confirm the `python` job runs the Postgres tests (not skipped) and passes: `gh run watch`. Commit message: `git commit -m "Run the Postgres tests in CI and before a release"`.

---

### Task 9: Measure, document, propose the ADR

**Files:**
- Create: `spikes/postgres-timing/README.md`, `spikes/postgres-timing/measure.py` (throwaway, never imported by `packages/`)
- Create: `docs/adr/0016-prumi-owns-its-warehouse-connection.md` (Status: Proposed)
- Modify: `README.md` (the source table, the 0.1 note if it names DuckDB only, Known limitations)

- [ ] **Step 1: Measure.** `measure.py` takes a `prumi.toml`. It times, with `time.perf_counter`, opening one Postgres connection, `Services.data_through()` cold, and one `Engine.run_block` for each block of the demo dashboard. Run it locally on the Postgres demo and, once, in CI: add a temporary step to the branch's `ci.yml`, read the numbers from the run log, and then remove the step in the same task. Write both sets of numbers in the spike's README.
- [ ] **Step 2: Judge.** If `data_through()` cold takes more than 2 seconds on the demo anywhere, open an issue with the numbers (title: `"Data through" is slow on Postgres`), unscheduled. Otherwise note "no action" in the spike's README.
- [ ] **Step 3: ADR 0016**, in the format of `docs/adr/0015-*.md`, Status: Proposed. Context: dbt teams already have connection settings in `profiles.yml`. Decision: Prumi keeps its own connection in `prumi.toml`, for a read-only role and for later per-viewer identity, and never takes a password there (libpq finds it). Consequences: the settings are written twice, and named connections (#20) may supersede where they live.
- [ ] **Step 4: README.** The source table: "dbt Semantic Layer (MetricFlow), on DuckDB and Postgres", 🟢 supported in 0.2, and "Snowflake, BigQuery and Databricks" stay planned. Known limitations: "One source: dbt on DuckDB and Postgres only…", and "the warehouse connection is a shared account". The roadmap's "Team use" line drops "dbt on Postgres". Don't change the version numbers or the CHANGELOG.
- [ ] **Step 5: Run the whole suite** with Postgres: `PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest`, `uv run ruff check`, `uv run ruff format --check`. Expected: all pass, no skips except the documented ones.
- [ ] **Step 6: Commit:** `git commit -m "Measure Postgres timings, document Postgres support, propose ADR 0016"`.
- [ ] **Step 7: Open the PR.** Title: `feat: run dbt projects on Postgres`. The body has `Closes #6`, the timings from the spike, the list of docs changed, and the attribution line.
