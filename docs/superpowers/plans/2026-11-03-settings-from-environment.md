# Settings From the Environment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every `prumi.toml` setting can come from a `PRUMI__…` environment variable (the environment wins, the file becomes optional), and the warehouse password comes from a variable the config names with `password_env`.

**Architecture:** `load_config` reads the file (if any) into a raw table, merges the `PRUMI__` variables into it before any check runs, and remembers which keys came from which variable so core's messages can name them. Core converts its own settings' text; the dbt adapter accepts text for its own settings, refuses unknown keys in `[source.dbt]`, and reads `password_env`.

**Tech Stack:** Python 3.12, pydantic v2, tomllib, psycopg 3.

**Spec:** `docs/superpowers/specs/2026-11-03-settings-and-postgres-registry-design.md`, section "PR 1" (issue #30)

## Global Constraints

- Branch `feat/settings-from-environment`, from an up-to-date `main` after the container image PR is merged. PR title `feat: settings from environment variables, and the warehouse password from a named variable`; body `Closes #30`.
- `NEXT.md` is never committed.
- Setting variables: `PRUMI__` + section path + key, upper case, joined by `__` (`PRUMI__SOURCE__DBT__CONNECTION__HOST`). Only the `PRUMI__` (double underscore) prefix is a setting; `PRUMI_CONTAINER`, `PRUMI_REGISTRY_PASSWORD`, `PRUMI_TEST_*`, `PRUMI_REQUIRE_*` are never read as settings.
- The environment wins over the file. An empty `PRUMI__…` variable counts as unset.
- Core's list settings (`script_hosts`, `style_hosts`, `font_hosts`) are comma-separated in a variable; core never guesses a type for adapter settings.
- A password never appears in a log record, message, MCP result, page or `prumi.toml`. Messages name a variable, never its value.
- The loopback rule applies whatever the source of `host`.
- ADR 0016 is not edited; a new ADR supersedes it.
- Comments never cite designs or plans. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- A variable whose key part has mixed case or a trailing `__` (`PRUMI__server__port`, `PRUMI__SERVER__`): environment names on Linux are case-sensitive; only upper-case names are settings, and a malformed one is refused naming the variable. Pinned in Task 1.
- A variable that sets a value inside a key the file has as a scalar (`PRUMI__SOURCE__ADAPTER__X` when `adapter = "dbt"`): refused naming the variable, not a Python `TypeError`. Pinned in Task 1.
- `password_env` naming a variable that holds only whitespace: treated as empty and refused. Pinned in Task 3.
- `PRUMI__SERVER__HOST=0.0.0.0` outside a container: refused with today's loopback message, which names the variable. Pinned in Task 2.
- `prumi certify` / `prumi list` started from the environment only (no file): works the same as `serve`. Pinned in Task 1 (`load_config` with no file) and Task 2 (CLI test).

---

### Task 1: Merge `PRUMI__` variables into the raw config

**Files:**
- Create: `packages/prumi-core/src/prumi/config_env.py`
- Modify: `packages/prumi-core/src/prumi/config.py` (`load_config`, `Config`, `_describe`, `_source`)
- Modify: `packages/prumi-core/src/prumi/cli.py` (`--config` default)
- Test: `packages/prumi-core/tests/test_config_env.py`

**Interfaces:**
- Produces:
  - `prumi.config_env.PREFIX = "PRUMI__"`
  - `prumi.config_env.from_environment(environ: Mapping[str, str]) -> dict[tuple[str, ...], tuple[str, str]]`: `("source", "dbt", "connection", "host") -> ("PRUMI__SOURCE__DBT__CONNECTION__HOST", "db.internal")`, empty values skipped; raises `ConfigError` naming the variable for a name with an empty part or a lower-case letter.
  - `prumi.config_env.merge(table: dict, settings: dict[tuple[str, ...], tuple[str, str]]) -> dict[tuple[str, ...], str]`: sets each value into `table` (creating sub-tables), returns `key path -> variable name` for messages; raises `ConfigError` naming the variable when a path runs through a non-table value.
  - `load_config(path: str | Path | None = None, environ: Mapping[str, str] | None = None) -> Config`: `path=None` means `./prumi.toml` if it exists, otherwise settings from the environment only; an explicit path that doesn't exist still raises today's `ConfigError`. `environ` defaults to `os.environ`.
  - `Config.path: Path | None` (None when there's no file); `Config.source_label: str` — the file path, or `"the environment"`; messages that printed `config.path` use `source_label`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_variable_sets_a_nested_key():
    env = {"PRUMI__SOURCE__ADAPTER": "dbt", "PRUMI__SOURCE__DBT__SEMANTIC_MANIFEST": "m.json"}
    config = load_config(None, env)  # tmp cwd with no prumi.toml
    assert config.sources["default"].settings["semantic_manifest"] == "m.json"

def test_the_environment_wins_over_the_file(tmp_path):
    # file has port = 8000, env PRUMI__SERVER__PORT=9000 -> server.port == 9000

def test_an_empty_variable_counts_as_unset(tmp_path):
    # file port = 8000, PRUMI__SERVER__PORT="" -> 8000

def test_other_prumi_variables_are_not_settings(tmp_path):
    # PRUMI_CONTAINER, PRUMI_REGISTRY_PASSWORD, PRUMI_TEST_POSTGRES_HOST set -> loads MINIMAL

@pytest.mark.parametrize("name", ["PRUMI__server__port", "PRUMI__SERVER__", "PRUMI____PORT"])
def test_a_malformed_variable_is_refused_by_name(name): ...  # ConfigError contains name

def test_an_unknown_section_from_a_variable_is_refused_by_name():
    # PRUMI__NOPE__X=1 -> ConfigError contains "PRUMI__NOPE__X"

def test_a_variable_through_a_scalar_is_refused_by_name():
    # file adapter = "dbt"; PRUMI__SOURCE__ADAPTER__X=1 -> ConfigError contains the name

def test_without_a_file_or_variables_the_message_names_both(tmp_path, monkeypatch):
    # cwd tmp_path, env {} -> ConfigError contains str(tmp_path / "prumi.toml") and "PRUMI__"

def test_an_explicit_missing_file_is_still_refused(tmp_path):
    # load_config(tmp_path / "x.toml", {"PRUMI__SOURCE__ADAPTER": "dbt"}) -> ConfigError

def test_relative_paths_from_variables_resolve_against_the_files_folder(tmp_path): ...
def test_relative_paths_without_a_file_resolve_against_the_current_folder(tmp_path, monkeypatch): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/prumi-core/tests/test_config_env.py -v`
Expected: FAIL (`ModuleNotFoundError: prumi.config_env`)

- [ ] **Step 3: Implement** `config_env.py` and wire it into `load_config`: read the file (or `{}`), `merge(...)`, then the existing section checks run on the merged table unchanged. `config_dir` is the file's folder, or `Path.cwd()` with no file. The CLI's `--config` default becomes `None` (help: `default: ./prumi.toml, if it exists; settings can also come from PRUMI__ variables`). `server/__init__.py`'s locale message uses `config.source_label`.

- [ ] **Step 4: Run the tests and the existing config tests**

Run: `uv run pytest packages/prumi-core/tests/test_config_env.py packages/prumi-core/tests/test_config.py packages/prumi-core/tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/prumi-core/src/prumi/config_env.py packages/prumi-core/src/prumi/config.py packages/prumi-core/src/prumi/cli.py packages/prumi-core/src/prumi/server/__init__.py packages/prumi-core/tests/test_config_env.py
git commit -m "Read settings from PRUMI__ environment variables, over the file"
```

### Task 2: Core's settings from text, and messages that name the variable

**Files:**
- Modify: `packages/prumi-core/src/prumi/config.py` (`_Server`, `_describe`, `_source`)
- Test: `packages/prumi-core/tests/test_config_env.py`, `packages/prumi-core/tests/test_cli.py`

**Interfaces:**
- Consumes: `merge(...)`'s `key path -> variable` map (Task 1).
- Produces: `_describe(where: str, section: str, exc: ValidationError, variables: Mapping[tuple[str, ...], str]) -> str` — a problem on a key that came from a variable is reported as `PRUMI__SERVER__PORT: <message>` instead of `server.port: <message>`.

- [ ] **Step 1: Write the failing tests**

```python
def test_text_values_become_core_types():
    env = {"PRUMI__SOURCE__ADAPTER": "dbt", "PRUMI__SERVER__PORT": "9000",
           "PRUMI__SERVER__AS_OF": "2026-10-15",
           "PRUMI__SERVER__SCRIPT_HOSTS": "cdn.jsdelivr.net, unpkg.com"}
    server = load_config(None, env).server
    assert server.port == 9000 and server.as_of == dt.date(2026, 10, 15)
    assert server.script_hosts == ("cdn.jsdelivr.net", "unpkg.com")

def test_a_bad_value_from_a_variable_is_reported_by_its_name():
    # PRUMI__SERVER__PORT="eighty" -> ConfigError starts with "the environment: PRUMI__SERVER__PORT:"

def test_a_non_loopback_host_from_a_variable_is_refused_outside_a_container(monkeypatch):
    # PRUMI__SERVER__HOST=0.0.0.0, PRUMI_CONTAINER unset -> ConfigError naming
    # PRUMI__SERVER__HOST, containing "isn't a loopback address"

def test_the_cli_runs_from_the_environment_alone(tmp_path, monkeypatch, ...):
    # cwd with no prumi.toml, PRUMI__ settings for the fake demo source -> `prumi list` exit 0
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/prumi-core/tests/test_config_env.py -v`
Expected: FAIL on list splitting and the variable names in messages.

- [ ] **Step 3: Implement** — a `field_validator(..., mode="before")` on the three host lists that splits a `str` on commas and strips blanks; pydantic's lax mode already turns `"9000"` and `"2026-10-15"` into `int`/`date`. `_describe` takes the variables map. `_source`'s own messages (`[source] needs adapter`, unknown keys in `[source]`) name the variable when the key came from one.

- [ ] **Step 4: Run the core suite**

Run: `uv run pytest packages/prumi-core -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/prumi-core/src/prumi/config.py packages/prumi-core/tests/test_config_env.py packages/prumi-core/tests/test_cli.py
git commit -m "Convert core settings from text, and name the variable in messages"
```

### Task 3: The dbt adapter: text settings, unknown keys, `password_env`

**Files:**
- Modify: `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/adapter.py` (`DbtAdapter.from_config`)
- Modify: `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/connections/postgres.py` (`_KEYS`, `PostgresSettings`, `connect_kwargs`, `connect_error_message`, `_refuse_connection_string`, the `password` refusal, module docstring)
- Test: `packages/prumi-adapter-dbt/tests/test_postgres_settings.py`, `packages/prumi-adapter-dbt/tests/test_startup.py`, `packages/prumi-adapter-dbt/tests/test_postgres_connection.py`, `packages/prumi-core/tests/test_logging.py` (sentinel)
- Modify: `conftest.py` (`PostgresDemo.connection_config` and the `postgres_demo` fixture use `password_env` instead of setting `PGPASSWORD`)

**Interfaces:**
- Produces: `PostgresSettings.password_env: str | None`; `PostgresSettings.from_config(config, environ: Mapping[str, str] | None = None)` reads the password into a private field that `__repr__` never shows (`field(repr=False)`); `connect_kwargs()` includes `password` only when `password_env` is set. `_KEYS` gains `"password_env"`. `[source.dbt]`'s known keys: `{"semantic_manifest", "connection"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_port_may_arrive_as_text():
    assert PostgresSettings.from_config(BASE | {"port": "5433"}).port == 5433

@pytest.mark.parametrize("port", ["", "five", "0x10", "-1", True])
def test_a_port_that_isnt_an_integer_is_refused(port): ...  # message names `port`, not the value

def test_unknown_keys_in_source_dbt_are_refused(...):
    # DbtAdapter.from_config({"semantic_manifest": ..., "connection": ..., "semantic_manifst": "x"})
    # -> AdapterStartupError mentioning "[source.dbt]" and "semantic_manifst"

def test_password_env_reads_the_named_variable():
    s = PostgresSettings.from_config(BASE | {"password_env": "WH_PW"}, {"WH_PW": "s3cret"})
    assert s.connect_kwargs()["password"] == "s3cret" and "s3cret" not in repr(s)

@pytest.mark.parametrize("environ", [{}, {"WH_PW": ""}, {"WH_PW": "   "}])
def test_a_named_variable_that_is_missing_or_empty_stops_startup(environ):
    # AdapterStartupError contains "WH_PW" and "password_env"

def test_without_password_env_no_password_is_passed():
    assert "password" not in PostgresSettings.from_config(BASE).connect_kwargs()

def test_the_password_key_points_to_password_env(): ...  # message contains "password_env"

def test_login_failure_points_to_password_env(postgres_demo): ...
    # wrong password via password_env -> message contains "password_env", not "PGPASSWORD"
```

And in `test_logging.py`'s sentinel test: the sentinel password delivered through `password_env` (wrong password) appears in no log record and no message.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/prumi-adapter-dbt -k "port or unknown or password" -v`
Expected: FAIL

- [ ] **Step 3: Implement** — `port`: accept an `int` (not `bool`) or a `str` of decimal digits, converted with `int()`, range 1–65535. `password_env`: a non-empty string matching `[A-Za-z_][A-Za-z0-9_]*`; the value read with `environ.get(name, "").strip()` must be non-empty, the stripped-of-nothing original is what's passed. Messages that said "PGPASSWORD or ~/.pgpass" or "the password's source" now say "the password in the variable `password_env` names". `_refuse_connection_string`'s hint becomes "the password goes in the variable `password_env` names". `DbtAdapter.from_config` refuses unknown keys in `[source.dbt]` with the existing `refuse_unknown_keys` helper style. In `conftest.py`, the fixture sets `PRUMI_TEST_READER_PASSWORD` and `connection_config()` adds `password_env = "PRUMI_TEST_READER_PASSWORD"`; the `PGPASSWORD` juggling goes.

- [ ] **Step 4: Run both packages' suites with Postgres**

Run: `docker run -d --name prumi-postgres -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:5432:5432 postgres:18` (once), then `PRUMI_TEST_POSTGRES_HOST=localhost PRUMI_REQUIRE_POSTGRES=1 uv run pytest -q`
Expected: PASS, no Postgres test skipped.

- [ ] **Step 5: Commit**

```bash
git add packages/prumi-adapter-dbt conftest.py packages/prumi-core/tests/test_logging.py
git commit -m "Read the warehouse password from the variable password_env names"
```

### Task 4: Docs and the ADR

**Files:**
- Create: `docs/adr/0018-the-warehouse-password-comes-from-a-named-variable.md` (status `Proposed · <date>`, `Supersedes: 0016`)
- Modify: `docs/adr/README.md` (list), `docs/configuration.md`, `packages/prumi-adapter-dbt/README.md`, `README.md`, `CONTRIBUTING.md` (if the Postgres-test section mentions `PGPASSWORD`), `spikes/postgres-timing/README.md` is left alone (spikes are throwaway)

- [ ] **Step 1: ADR 0018**: restates 0016's decision that Prumi keeps its own read-only connection, and replaces "libpq finds the password" with `password_env`; considered: `PGPASSWORD`/pgpass (unfamiliar, one password for two connections once the registry is on Postgres), a fixed variable name, Airflow-style connection URLs; consequence: libpq's own lookup still works underneath when `password_env` is unset, undocumented. ADR 0016 itself is not edited (AGENTS.md); the ADR index marks it superseded if the index has a status column.

- [ ] **Step 2: `docs/configuration.md`**: the intro says settings can come from a file, the environment, or both; a `## Settings from environment variables` section (names table from the spec, environment wins, empty = unset, comma-separated lists, `prumi.toml` optional, relative paths); in `[source.dbt.connection]`, `password_env` in the Postgres table, and the libpq paragraph rewritten around it.

- [ ] **Step 3: adapter README**: the Postgres connection example with `password_env`, and in the adapter-protocol part, that adapter settings may arrive as strings. `README.md`: the configure example (if it shows Postgres) and the troubleshooting line ("the password's source" -> "`password_env`").

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q && uv run ruff check && uv run ruff format --check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0018-the-warehouse-password-comes-from-a-named-variable.md docs/adr/README.md docs/configuration.md packages/prumi-adapter-dbt/README.md README.md CONTRIBUTING.md docs/superpowers/specs/2026-11-03-settings-and-postgres-registry-design.md docs/superpowers/plans/2026-11-03-settings-from-environment.md docs/superpowers/plans/2026-11-03-postgres-registry.md
git commit -m "Document settings from the environment and password_env; propose ADR 0018"
```

### Task 5: PR and merge

- [ ] **Step 1:** Push, open the PR (title from Global Constraints, `Closes #30`, docs changed, attribution line).
- [ ] **Step 2:** `gh pr checks --watch`; fix failures on the branch.
- [ ] **Step 3:** Squash-merge once green, switch to `main`, pull.
