# Postgres Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prumi's registry (dashboards, versions, certifications, metric requests, page checks) can live in Postgres, chosen with `[registry] backend = "postgres"`, while SQLite stays the default and an existing 0.2 SQLite registry opens unchanged.

**Architecture:** `Registry` keeps its public methods and its SQL, and runs on a small store layer: `SqliteStore` (today's code) and `PostgresStore` (psycopg 3). The SQL is written once in the dialect both accept (`?` placeholders translated for psycopg, `ON CONFLICT … DO UPDATE`, `RETURNING id`). Migrations keep their numbers and the `schema_version` table; a migration whose SQL differs on Postgres gets a file of the same name under `migrations/postgres/`. Writers on Postgres take a transaction-level advisory lock, the counterpart of SQLite's `BEGIN IMMEDIATE`.

**Tech Stack:** Python 3.12, sqlite3, psycopg 3 (`psycopg[binary]`), Postgres 18 in CI.

**Spec:** `docs/superpowers/specs/2026-11-03-settings-and-postgres-registry-design.md`, section "PR 2" (issue #28)

## Global Constraints

- Branch `feat/postgres-registry`, from an up-to-date `main` after the settings PR is merged. PR title `feat: keep Prumi's registry in Postgres`; body `Closes #28`.
- `NEXT.md` is never committed.
- Without a `[registry]` section the registry is SQLite at `<data_dir>/registry.sqlite3`, exactly as today.
- `[registry.postgres]` keys: `host`, `port` (default 5432), `dbname`, `user`, `sslmode` (libpq's six values); `password` and unknown keys refused; the section is refused with `backend = "sqlite"` and required with `"postgres"`.
- The password comes from `PRUMI_REGISTRY_PASSWORD` only; unset means no password is passed. It never appears in a log, message or config.
- Versions are never updated; status is computed on read; timestamps, spec JSON and HTML are stored as the same text on both backends (ADR 0007, ADR 0010).
- A first use on a database that already holds any table outside `pg_catalog` / `information_schema` is refused: the registry needs its own database.
- psycopg is an optional extra of core: `prumi[postgres]`. Core never imports the dbt adapter.
- ADR 0013 is not edited; ADR 0019 supersedes it.
- Comments never cite designs or plans. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- `prumi list` order: dashboard ids with mixed case or `-`/`_` must print in the same order on both backends (SQLite compares bytes; Postgres collations don't). Pinned in Task 2 (ids `"B"`, `"a"`, `"a-b"`, `"a_b"` sorted the same on both).
- A Postgres server that is down when `prumi serve` starts: the server stops before binding, with the registry message, not a traceback at the first request. Pinned in Task 4.
- `prumi certify` from the CLI while the server runs on the same Postgres registry: the next status read sees it. Pinned in Task 4.
- A role that can log in but can't create tables: refused at startup naming the role and the database. Pinned in Task 3.
- `PRUMI_REGISTRY_PASSWORD` set but `backend = "sqlite"`: ignored silently (no error, no use). Pinned in Task 1.

---

### Task 1: `[registry]` in the config

**Files:**
- Modify: `packages/prumi-core/src/prumi/config.py` (`Config`, `load_config`'s known sections)
- Test: `packages/prumi-core/tests/test_config.py`

**Interfaces:**
- Produces: `RegistryPostgresConfig(host: str, port: int, dbname: str, user: str, sslmode: str | None)` (frozen dataclass); `RegistryConfig(backend: Literal["sqlite", "postgres"], postgres: RegistryPostgresConfig | None)`; `Config.registry: RegistryConfig` (default `RegistryConfig("sqlite", None)`).

- [ ] **Step 1: Write the failing tests**

```python
def test_without_a_registry_section_it_is_sqlite(tmp_path):
    assert load_config(write(tmp_path, MINIMAL)).registry == RegistryConfig("sqlite", None)


def test_a_postgres_registry(tmp_path):
    text = (
        MINIMAL
        + '[registry]\nbackend = "postgres"\n[registry.postgres]\nhost = "db"\ndbname = "prumi"\nuser = "prumi"\n'
    )
    r = load_config(write(tmp_path, text)).registry
    assert r.backend == "postgres" and r.postgres == RegistryPostgresConfig(
        "db", 5432, "prumi", "prumi", None
    )


def test_postgres_settings_come_from_variables_too(
    tmp_path,
): ...  # PRUMI__REGISTRY__POSTGRES__PORT="6543"


@pytest.mark.parametrize(
    "text, words",
    [
        ('[registry]\nbackend = "postgres"\n', "[registry.postgres]"),
        ('[registry]\nbackend = "sqlite"\n[registry.postgres]\nhost = "x"\n', "backend"),
        ('[registry]\nbackend = "mysql"\n', "backend"),
        (
            '[registry]\nbackend = "postgres"\n[registry.postgres]\nhost="d"\ndbname="p"\nuser="p"\npassword="x"\n',
            "PRUMI_REGISTRY_PASSWORD",
        ),
        (
            '[registry]\nbackend = "postgres"\n[registry.postgres]\nhost="d"\ndbname="p"\nuser="p"\nschema="x"\n',
            "schema",
        ),
    ],
)
def test_bad_registry_settings_are_refused(tmp_path, text, words): ...  # ConfigError contains words


def test_the_registry_password_variable_is_ignored_with_sqlite(tmp_path, monkeypatch): ...
```

- [ ] **Step 2: Run them to see them fail** — `uv run pytest packages/prumi-core/tests/test_config.py -k registry -v`; Expected: FAIL.

- [ ] **Step 3: Implement** with a pydantic model per section (`extra="forbid"`) the way `_Server` is done; `host`/`dbname` refuse connection strings like the warehouse's `_refuse_connection_string` (core gets its own copy of that small check).

- [ ] **Step 4: Run** — `uv run pytest packages/prumi-core/tests/test_config.py packages/prumi-core/tests/test_config_env.py -v`; Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/prumi-core/src/prumi/config.py packages/prumi-core/tests/test_config.py
git commit -m "Add the [registry] section"
```

### Task 2: The store layer, and the registry on it

**Files:**
- Create: `packages/prumi-core/src/prumi/registry/stores.py`
- Create: `packages/prumi-core/src/prumi/registry/migrations/postgres/0001_initial.sql` (and `0002`, `0003` only if their SQL differs on Postgres)
- Modify: `packages/prumi-core/src/prumi/registry/__init__.py` (`Registry.__init__`, `_connect`, `_transaction`, `_migrate`, `record_check`, `request_metric`, the module docstring)
- Modify: `packages/prumi-core/pyproject.toml` (extra `postgres = ["psycopg[binary]"]`), `uv.lock` (`uv lock`)
- Modify: `conftest.py` (root: a `postgres_server` fixture), `packages/prumi-core/tests/conftest.py` (a `registry_store` fixture), `packages/prumi-core/tests/test_registry.py`

**Interfaces:**
- Produces:
  - `class Store(Protocol)`: `name: str` (`"sqlite"` / `"postgres"`); `connect() -> ContextManager[Conn]`; `transaction() -> ContextManager[Conn]`; `migration_files() -> list[tuple[int, Traversable]]`; `before_first_migration(conn: Conn) -> None`.
  - `class Conn(Protocol)`: `execute(sql: str, params: Sequence[Any] = ()) -> Rows`, where rows support `fetchone()`, `fetchall()`, `rowcount`, and each row supports `row["col"]`, `dict(row)` and tuple unpacking.
  - `SqliteStore(data_dir: Path)` — today's behavior (WAL, `foreign_keys=ON`, `BEGIN IMMEDIATE`).
  - `PostgresStore(settings: RegistryPostgresConfig, password: str | None)` — `autocommit=True` connections, `dict_row`-style rows that also unpack as tuples, `?` -> `%s` translation, transactions as `BEGIN` + `SELECT pg_advisory_xact_lock(<fixed key>)`, `connect_timeout=10`, `DateStyle=ISO`.
  - `Registry(store: Store | Path, now=...)` — a `Path` means `SqliteStore(path)`, so every current caller keeps working.

- [ ] **Step 1: Make the registry tests run on both stores** — in `packages/prumi-core/tests/conftest.py`, a fixture `registry_store` parametrized `["sqlite", "postgres"]`: SQLite gives `SqliteStore(tmp_path / "data")`; Postgres uses a new session fixture `postgres_server` in the root `conftest.py` (it returns `PostgresServer.from_env()`, and holds the skip-or-fail logic and message that `postgres_demo` uses today, which `postgres_demo` then reuses; it is marked `postgres` in `pytest_collection_modifyitems`), creates a fresh database `prumi_registry_<uuid hex>` with the admin login, yields `PostgresStore(...)` for it, and drops it afterwards. `test_registry.py`'s `registry` fixture builds on `registry_store`. Tests that are about SQLite files (`test_schema_version_wal_and_reopening`, `test_two_registries_on_one_file_see_each_other`, `test_opening_an_older_registry_drops_the_reports_it_stored_with_data`) stay SQLite-only. Add:

```python
def test_both_backends_store_the_same_content(registry_store, tmp_path):
    # publish the same spec + HTML on registry_store and on a fresh SqliteStore:
    # fingerprints, spec_hash, html_hash, spec_json, html and status are equal

def test_dashboards_are_listed_in_the_same_order(registry):
    # ids "B", "a", "a-b", "a_b" -> [d.id for d in registry.dashboards()] == sorted(ids)

def test_a_0_2_registry_opens_unchanged(tmp_path):
    # build a SQLite registry with migrations 1-3 only (copy of the current files), publish,
    # then open with Registry(tmp_path) -> same dashboards, schema_version max == 3
```

- [ ] **Step 2: Run them to see the Postgres ones fail**

Run: `PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest packages/prumi-core/tests/test_registry.py -v`
Expected: SQLite cases PASS, Postgres cases FAIL (`ModuleNotFoundError: prumi.registry.stores` or similar).

- [ ] **Step 3: Implement** — move the sqlite3 code into `SqliteStore`; `Registry` uses `self.store`. Rewrite `record_check`'s `INSERT OR REPLACE` as `INSERT … ON CONFLICT (dashboard_id, version, state_key) DO UPDATE SET …` and `request_metric`'s `lastrowid` as `RETURNING id` (both work on SQLite ≥ 3.35; check `sqlite3.sqlite_version` and keep a SQLite fallback only if CI's is older). The Postgres `0001_initial.sql`: identity columns (`BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY`) where SQLite has `INTEGER PRIMARY KEY AUTOINCREMENT`, and `COLLATE "C"` on every `TEXT` key column used in `ORDER BY` (`dashboards.id`, `page_checks.state_key`, `reported_at`), so ordering matches SQLite's byte order. `_migrate` takes the store's file list (a `migrations/postgres/<same name>` replaces the shared file on Postgres) and runs each in a store transaction. The existing SQLite migration files are not changed.

- [ ] **Step 4: Run the registry suite on both, and the core suite**

Run: `PRUMI_TEST_POSTGRES_HOST=localhost PRUMI_REQUIRE_POSTGRES=1 uv run pytest packages/prumi-core -q`
Expected: PASS, no skips for the registry's Postgres cases.

- [ ] **Step 5: Commit**

```bash
git add conftest.py packages/prumi-core/src/prumi/registry packages/prumi-core/pyproject.toml uv.lock packages/prumi-core/tests/conftest.py packages/prumi-core/tests/test_registry.py
git commit -m "Run the registry on SQLite or Postgres"
```

### Task 3: Postgres startup checks

**Files:**
- Create: `packages/prumi-core/src/prumi/registry/postgres_errors.py`
- Modify: `packages/prumi-core/src/prumi/registry/stores.py` (`PostgresStore.before_first_migration`, connection errors)
- Test: `packages/prumi-core/tests/test_registry_postgres.py`

**Interfaces:**
- Produces: `prumi.registry.RegistryError(Exception)` — the registry can't be used; the message names what to change and never a password. `prumi.registry.postgres_errors.connect_message(exc: psycopg.Error, settings: RegistryPostgresConfig) -> str`.

- [ ] **Step 1: Write the failing tests** (Postgres-only; `postgres` marker)

```python
def test_a_database_that_holds_other_tables_is_refused(pg_admin, fresh_db):
    # create table public.orders in fresh_db -> Registry(PostgresStore(...)) raises RegistryError
    # containing "its own database" and the dbname

def test_once_the_registry_exists_the_check_does_not_run_again(...):
    # open once (creates tables), add an unrelated table, open again -> no error

def test_a_wrong_password_is_refused_without_showing_it(...):
    # PRUMI_REGISTRY_PASSWORD sentinel -> RegistryError mentions "PRUMI_REGISTRY_PASSWORD",
    # sentinel not in str(exc), exc.__context__ is None

def test_a_missing_database_is_named(...): ...         # mentions dbname and `dbname`
def test_an_unreachable_server_names_host_and_port(...): ...  # port 1 on 127.0.0.1
def test_a_role_that_cannot_create_tables_is_refused(...):
    # role with LOGIN and CONNECT only -> mentions the role and "create tables"

def test_postgres_without_psycopg_names_the_extra(monkeypatch): ...
    # psycopg import blocked -> RegistryError containing "prumi[postgres]"
```

- [ ] **Step 2: Run them to see them fail** — `PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest packages/prumi-core/tests/test_registry_postgres.py -v`; Expected: FAIL.

- [ ] **Step 3: Implement** — classification like the dbt adapter's `_sqlstate` (SQLSTATE, `needs_password`, a few server-message patterns), in core's own words: login refused -> check `user` under `[registry.postgres]` and `PRUMI_REGISTRY_PASSWORD`; `3D000` -> no database, check `dbname`; `42501` on `CREATE TABLE` -> the role can't create tables in the database. Every `RegistryError` is raised outside the `except` block (`from None`), as the adapter does. `before_first_migration` runs only when `schema_version` doesn't exist.

- [ ] **Step 4: Run** — same command with `PRUMI_REQUIRE_POSTGRES=1`; Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/prumi-core/src/prumi/registry packages/prumi-core/tests/test_registry_postgres.py
git commit -m "Check the Postgres registry at startup"
```

### Task 4: Services, the server and the CLI on the configured registry

**Files:**
- Modify: `packages/prumi-core/src/prumi/services.py` (`Services.registry`)
- Modify: `packages/prumi-core/src/prumi/server/__init__.py` (`serve`: open the registry before binding)
- Modify: `packages/prumi-core/src/prumi/cli.py` (`main`: catch `RegistryError`)
- Test: `packages/prumi-core/tests/test_registry_postgres.py`, `packages/prumi-core/tests/test_cli.py`

**Interfaces:**
- Consumes: `Config.registry` (Task 1), `PostgresStore`, `RegistryError` (Tasks 2–3).
- Produces: `Services.registry` builds `Registry(SqliteStore(data_dir))` or `Registry(PostgresStore(config.registry.postgres, os.environ.get("PRUMI_REGISTRY_PASSWORD") or None))`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_server_and_the_cli_share_the_postgres_registry(live server on a Postgres registry):
    # publish over MCP, `prumi certify <id> 1 --by Ana` via cli.main([...]) with the same
    # settings, then GET the status API -> "certified"

def test_serve_stops_before_binding_when_the_registry_is_down(...):
    # registry port 1 -> `prumi serve` exits REFUSED with the registry message; nothing listens

def test_the_cli_reports_a_registry_error_without_a_traceback(...): ...  # stderr starts "prumi: "
```

- [ ] **Step 2: Run them to see them fail**; Expected: FAIL.

- [ ] **Step 3: Implement** — `serve()` touches `services.registry` right after the source loads; `main()` adds `RegistryError` to the caught exceptions.

- [ ] **Step 4: Run everything with Postgres** — `PRUMI_TEST_POSTGRES_HOST=localhost PRUMI_REQUIRE_POSTGRES=1 PRUMI_REQUIRE_BROWSER=1 uv run pytest -q`; Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/prumi-core/src/prumi/services.py packages/prumi-core/src/prumi/server/__init__.py packages/prumi-core/src/prumi/cli.py packages/prumi-core/tests
git commit -m "Use the configured registry in the server and the CLI"
```

### Task 5: Docs and the ADR

**Files:**
- Create: `docs/adr/0019-the-registry-is-sqlite-or-postgres.md` (`Proposed · <date>`, `Supersedes: 0013`)
- Modify: `docs/adr/README.md`, `docs/configuration.md`, `README.md`, `packages/prumi-core/README.md` (if it documents install extras), `CONTRIBUTING.md` (the registry tests on Postgres use the same `PRUMI_TEST_POSTGRES_*` server; say so in "The Postgres tests")

- [ ] **Step 1: ADR 0019**: everything in 0013 that still holds (plain SQL, numbered migrations, `schema_version`, immutable versions, status on read, no company data, delete semantics), plus: the backend chosen in `[registry]`, SQLite the default, Postgres recommended for a real install; shared migration numbers with per-backend files where SQL differs; always its own database (the empty-database check, and why: the warehouse role is read-only); `PRUMI_REGISTRY_PASSWORD`; one process per registry; `pg_dump` for backups. Considered: an ORM; the registry in the warehouse database; moving SQLite data (no installs yet).

- [ ] **Step 2: `docs/configuration.md`**: `## [registry]` and `### [registry.postgres]` tables, `PRUMI_REGISTRY_PASSWORD`, the "own database" rule, backups for each backend; `data_dir`'s row says it holds the SQLite registry. Postgres is presented first as the recommended registry for a real install, SQLite as the default on one machine.

- [ ] **Step 3: `README.md`**: known limitations and "Where your data goes" if it names where the registry lives.

- [ ] **Step 4: Run everything** — `uv run pytest -q && uv run ruff check && uv run ruff format --check`; Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0019-the-registry-is-sqlite-or-postgres.md docs/adr/README.md docs/configuration.md README.md CONTRIBUTING.md packages/prumi-core/README.md
git commit -m "Document the Postgres registry and propose ADR 0019"
```

### Task 6: PR and merge

- [ ] **Step 1:** Push, open the PR (title from Global Constraints, `Closes #28`, docs changed, attribution line).
- [ ] **Step 2:** `gh pr checks --watch`; fix failures on the branch.
- [ ] **Step 3:** Squash-merge once green, switch to `main`, pull.
