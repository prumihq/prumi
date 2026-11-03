# dbt on Postgres

Status: approved by the maintainer on 2026-11-02. Issue: #6 (milestone 0.2).

## Why

Prumi 0.1 only runs dbt projects on DuckDB. Most teams that would use Prumi run dbt on a warehouse, and Postgres is the one that can run in CI. 0.2 makes Prumi ready to run on Postgres. No real user is expected to run it on a real warehouse before 0.4 (team server). So this release is about **proving it works with the same guarantees as DuckDB**, not about polishing the experience for strangers.

Success: the same checks that pass on DuckDB pass on Postgres and give the same numbers, in CI, on every pull request.

## Goals

1. **A `postgres` connection type** under `[source.dbt.connection]`. `prumi serve` starts on a dbt project built on Postgres, with the same startup checks as DuckDB, and serves the same pages and MCP tools.
2. **MetricFlow writes the SQL, Prumi only runs it**, as on DuckDB (ADR 0002). The compile-only client gets MetricFlow's Postgres dialect.
3. **One demo project, both warehouses.** `examples/demo-dbt-project` builds on DuckDB (as today) or on Postgres, from the same raw rows.
4. **The same checks on both.** The tests that run real queries run on DuckDB and on Postgres and expect the same numbers. CI runs both on every pull request.
5. **Credentials never appear in logs or error messages.** This is the maintainer's rule for this work, and tests check it.
6. **Measure the "data through" cost on Postgres** and record the numbers (see "Speed").

## Non-goals

- **Reading the dbt profile.** Prumi keeps its own connection (see "Decided").
- **Named connections or secret stores.** That's #20, unscheduled. 0.2 adds nothing that it would have to undo.
- **Per-viewer identity.** Postgres runs as one fixed user (`identity_mode = "shared_account"`), like DuckDB. 0.4 replaces this.
- **A Postgres path in the demo's walkthrough.** The five-minute trial in `examples/demo-dbt-project/README.md` stays on DuckDB. The `docker compose` trial (0.3) is where a Postgres trial would go, if ever.
- **A second demo project** for Postgres.
- **Speeding up "data through"** in 0.2, unless the measurement shows a real problem (see "Speed").
- `prumi --version` (#7) and manifest reload (#8). They're in the same milestone but are separate work.
- Snowflake, BigQuery, Databricks.

## Anti-goals

The work fails, even if Postgres "works", if:

- a password, or a connection string holding one, shows up in a log line, an error message, an MCP result or a page;
- Prumi computes or adjusts a number differently on Postgres than on DuckDB (no Postgres-only rounding, casting or re-aggregation; the numbers come from MetricFlow's SQL);
- Prumi can write to the warehouse;
- keeping the demo on two warehouses becomes a second project to maintain.

## Constraints

- AGENTS.md's rules: numbers only come from the source, access belongs to the source, core never imports an adapter, database dialects aren't Prumi's job (MetricFlow renders the SQL).
- MetricFlow stays pinned at `0.213.0`. Its Postgres renderer is used as it is.
- Logs already hold no row or filter values. Database errors already reach agents and logs as the exception's type name only (`source.py`, `validate/__init__.py`). That stays true on Postgres.

## Decided by the maintainer

- **Prumi has its own connection,** in `prumi.toml`, not read from the dbt profile. The profile's user is the one that builds tables and can write. Prumi only reads, so it gets its own read-only role. And in 0.4 the user won't be fixed: the viewer's identity replaces it.
- **No password in `prumi.toml`.** Postgres's client library (libpq) finds it where it always looks: the `PGPASSWORD` environment variable, `~/.pgpass`, or a service file. Prumi writes no code for that.
- **`timezone` is a setting in `prumi.toml`.** When it's set, every query runs in that time zone. When it's left out, the connection's own time zone applies (from the server, role or database). This matters because on Postgres a `timestamptz` value's day depends on the session time zone. A read-only role with a different time zone from dbt's would put a 23:30 order on a different day than the team's other tools.
- **One demo project on both warehouses**, with the tests run on both, and the same numbers expected.
- **Postgres in CI from a service container; locally from Docker.** Without a Postgres, the Postgres tests skip locally, as the browser tests do without Chromium.
- **Speed: measure, don't fix,** in 0.2.
- **The demo's walkthrough stays on DuckDB.**

## The connection

```toml
[source.dbt.connection]
type = "postgres"
host = "localhost"
port = 5432              # optional, default 5432
dbname = "analytics"
user = "prumi_reader"
sslmode = "require"      # optional
timezone = "UTC"         # optional; the connection's own when left out
```

- It opens for each query and closes afterwards, as DuckDB does, so a per-viewer connection later only replaces the factory.
- **My call:** every connection is read-only at the session level (`default_transaction_read_only`), as DuckDB is opened `read_only=True`. Prumi can't write even if the role can.
- **My call:** `sslmode` goes straight to libpq; when it's left out, libpq's own default applies.
- **My call:** `password` is a known key in `prumi.toml` only so it can be refused: the server stops with a message telling the user to use `PGPASSWORD` or `~/.pgpass`, and the message never contains the value.
- **Unknown keys.** `docs/configuration.md` says an unknown key stops the server, but today nothing checks the keys of `[source.dbt.connection]` (DuckDB ignores everything but `path`). **My call:** both connection types refuse unknown keys, with a message that names the key and never its value. For DuckDB this is a fix that makes the code match the documented behavior, not a breaking change.
- `timezone` is checked at startup: an unknown name stops the server with a message naming the setting.
- **The driver is psycopg 3** (the maintainer accepted its LGPL license: Prumi depends on it as a package and doesn't copy or change it). It uses libpq, which is what finds the password. It's an optional extra `prumi-adapter-dbt[postgres]`, next to `[duckdb]`. **My call:** `prumi[dbt]` installs both drivers, so `uv tool install "prumi[dbt]"` gives a server that starts on either warehouse.

## Startup checks

The same as DuckDB, with messages that name what to fix:

- **Can't connect** (host unreachable, authentication failed, database doesn't exist, TLS refused): the message says which kind of failure it is and which settings to check (`host`, `port`, `dbname`, `user`, `sslmode`, or the password's source). It's built from Prumi's own text and the non-secret settings. It never interpolates the driver's message or a connection string.
- **Missing tables:** as today, the manifest's tables that don't resolve on the connection, listed by name.
- **A table the role can't read** gets its own message (grant `SELECT` to the role), separate from "doesn't exist".
- **Database mismatch:** dbt bakes the database name into the manifest's relations (`"analytics"."public"."orders"`), and Postgres doesn't do cross-database queries. If the manifest's database isn't the connection's `dbname`, say so plainly instead of reporting every table as missing.

## Credentials in logs and messages

- Prumi's messages and logs never contain the password or a connection string. Any message about a connection failure is Prumi's own text, built from `host`, `port`, `dbname`, `user` and `sslmode` only.
- Database errors at query time keep today's rule: only the exception's type name leaves the adapter, in logs, MCP results and pages.
- **Tests:** following the sentinel pattern of `test_logging.py`, a test makes connections fail on purpose (wrong password, wrong host, wrong database, a `password` key in `prumi.toml`) with a sentinel password, captures every log record at DEBUG and every message the user sees (`prumi serve` stderr, startup errors), and checks the sentinel is in none of them. The wrong-password case needs a real Postgres; the others don't.

## The demo on both warehouses

- `profiles.yml` gets a `postgres` output. It reads the password with `env_var()`, the way teams do it. The DuckDB output stays the default.
- The time spine (`metricflow_time_spine.sql`) uses DuckDB's `range(...)`. It becomes portable, for example through dbt's `date_spine` macro. The DuckDB build's results must not change.
- `generate.py` loads the same seeded raw rows into the `raw` schema of either warehouse.
- `build.py` takes the warehouse to build. The Postgres build writes its own semantic manifest into its own target folder, because the manifest bakes in the database name. The DuckDB outputs stay where they are, so nothing that uses them today changes.
- `dbt-postgres` joins the demo's dependencies.

## Tests on both warehouses

- **At least** these run on both: the MetricFlow contract test (`test_contract.py`, whose hand-written SQL may need a portable form), `test_execute.py`, `test_startup.py`, and a server-level check that starts Prumi on the Postgres demo, loads the home page and renders and validates a demo dashboard. The builder may add more.
- Both warehouses must give **the same numbers**, compared the way the contract test already compares them (floats and decimals rounded to 6 places, timestamps as dates). The per-warehouse checks (MetricFlow's result against hand-written SQL) would pass even if the two warehouses disagreed, so **a test also compares DuckDB's results with Postgres's directly**, on the same queries.
- **When they differ:** never adjust numbers in Prumi. If the cause is in the demo's dbt models (a missing cast, say), fixing the demo is fine: it's the "customer" side. If the cause is MetricFlow's Postgres SQL or how Postgres itself computes, stop and bring it to the maintainer with the query and both results.
- **My call:** the both-warehouse comparison also runs with the Postgres `timezone` set far from UTC (for example `Pacific/Kiritimati`, UTC+14), because on Postgres `date_trunc` on a `date` goes through `timestamptz`. The demo's plain dates must land on the same days whatever the time zone.
- Postgres-only checks (startup failures, credentials, `timezone`, the read-only session) are additional.
- **Locally:** the Postgres tests run when a Postgres is described by environment variables (the builder picks the names), and skip otherwise. CONTRIBUTING.md shows the one `docker run` command that starts one.
- **CI:** the `python` job gets a Postgres service container and a variable that makes a missing Postgres fail instead of skip, like `PRUMI_REQUIRE_BROWSER`. **My call:** one current major version of the official `postgres` image, pinned by tag.
- The install check (`scripts/check_install.py`) stays on DuckDB.

## Speed

The home page's "data through" runs one query per catalog metric, at startup and when its 10-minute cache expires. Also, each dashboard block asks for each of its metrics' "data through" with no cache. And each query opens a new connection, which costs more on Postgres (and more with TLS) than on DuckDB.

In 0.2: time the home page's "data through", a dashboard's first render, and one connection's setup, on the Postgres demo in CI and locally, and put the numbers in the pull request and on #6. **My call:** if the home page's startup takes more than a couple of seconds on the demo, open an issue for it rather than changing the design here.

## Types

Prumi already turns `Decimal` into float and timestamp buckets into dates (`_query.py`). Postgres's `NUMERIC`, `timestamp` and `timestamptz` should go through that unchanged; the both-warehouse tests show it. The demo stores plain dates, so **the builder adds one Postgres-only test with a `timestamptz` column** showing that `timezone` changes which day an event lands in, and that leaving it out uses the connection's time zone.

## Docs

- `README.md`: the source table (dbt on Postgres supported), the install line if it changes, the troubleshooting list of startup messages, and the known limitations ("dbt on DuckDB only" becomes DuckDB and Postgres; "the DuckDB connection is a shared account" covers Postgres too).
- `docs/configuration.md`: the `postgres` keys, and where the password comes from.
- `packages/prumi-adapter-dbt/README.md`: the `[postgres]` extra and the Postgres connection.
- `CONTRIBUTING.md`: running the Postgres tests locally with Docker.
- `examples/demo-dbt-project/README.md`: the project now builds on either warehouse (the walkthrough itself doesn't change).
- **A Proposed ADR:** "Prumi's warehouse connection is its own, and its password stays out of `prumi.toml`." A future reader would likely expect Prumi to reuse the dbt profile.

## Left to the builder

- How `timezone` and the read-only session are applied (connection options or `SET` per connection).
- The environment variable names for the test Postgres, the CI service setup, and how the build fixture builds the Postgres demo.
- How `test_contract.py` runs on both: one parametrized suite, or a shared core with a fixture per warehouse.
- Connection timeouts: a sensible `connect_timeout` so an unreachable host doesn't hang startup.
