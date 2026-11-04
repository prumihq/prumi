# Settings from the environment, passwords from named variables, and the registry in Postgres

Status: draft, for the maintainer's review. Issues: #30 and #28 (milestone 0.3). Second of three 0.3 designs, after `2026-11-03-container-image-design.md` (#29) and before `2026-11-03-compose-install-design.md` (#12).

It ships as **two pull requests**, in this order (*my call*):

1. **Settings and passwords** (#30): every setting from an environment variable, and the warehouse password from a variable `prumi.toml` names.
2. **The Postgres registry** (#28): Prumi's own data in Postgres, with SQLite kept as the default.

## Why

The compose install (third design) wants its whole configuration in one reviewed file, and `prumi.toml` can't be the only place settings come from. Passwords come today from libpq's `PGPASSWORD` or `~/.pgpass`. The maintainer, and most people, don't know that mechanism, and it breaks down once Prumi has two Postgres connections (the warehouse, and the registry) with two different roles: one `PGPASSWORD` holds one password.

The registry moves to Postgres in 0.3 rather than with the team server:

- the compose install keeps the same shape from 0.3 onward, instead of gaining a Postgres container later and making early installs change their setup and move their data;
- the team server's new tables (users, tokens) are written and tested on Postgres from the start;
- the release after 0.3 already carries login, tokens, HTTPS and sharing.

Success: the compose file can hold every setting and both passwords, with no `prumi.toml`; and every registry test passes on SQLite and on Postgres, in CI.

## Goals

1. **Every `prumi.toml` setting can come from an environment variable**, and the environment wins over the file. `prumi.toml` becomes optional.
2. **The warehouse password comes from a variable** that `prumi.toml` names (`password_env`), the way dbt's `env_var()` works.
3. **The registry can live in Postgres**, chosen in configuration, with its password in `PRUMI_REGISTRY_PASSWORD`.
4. **Fingerprints, certifications and status mean the same on both backends.**

## Non-goals

- Moving an existing SQLite registry into Postgres. There are no installs to move yet.
- Several Prumi processes sharing one registry. One process per registry, as with SQLite. The migrations shouldn't rule it out later, but no concurrency work is needed now.
- Editing connections in a web UI, as Airflow does. A page that edits warehouse connections with no login would let anyone on the machine change them. It belongs with #20 (named connections), after login.
- Secret managers (Vault, cloud secret stores), and Docker's `*_FILE` secret convention. *My call:* not now; a variable can carry what a compose secret holds.
- An ORM, or a database abstraction library.
- Blocking libpq's own password lookup (`PGPASSWORD`, `~/.pgpass`) in code (see "Passwords").
- The compose file and its docs: the third design.

## Anti-goals

The work fails, even if it works, if:

- a password shows up in a log line, an error message, an MCP result, a page, or `prumi.toml`;
- the same dashboard version gets a different fingerprint or status on the two backends, or a certification means something different;
- an existing 0.2 SQLite registry doesn't open unchanged;
- an environment variable can open Prumi to the network outside a container (the loopback rule holds whatever the source of `host`);
- the registry and the warehouse can end up being the same database;
- a typo in a setting's variable name is silently ignored.

## Constraints

- AGENTS.md's rules: core never imports an adapter; access belongs to the source.
- ADR 0007: versions are immutable, status is computed on read.
- ADR 0010: the registry holds no company data. That doesn't change on Postgres.
- ADR 0016 is accepted, so it is superseded by a new ADR, not edited.
- psycopg 3 is already Prumi's Postgres driver (the dbt adapter's `[postgres]` extra), and its LGPL license is accepted.

## Decided by the maintainer

- **Environment over file:** when both set the same value, the environment variable wins.
- **The warehouse password comes from a variable named in the config:** `password_env = "WAREHOUSE_PASSWORD"` in `[source.dbt.connection]`. The password never goes in `prumi.toml`; only the variable's name does.
- **The registry password is always `PRUMI_REGISTRY_PASSWORD`.** There is no setting for it.
- **`PGPASSWORD` and `~/.pgpass` disappear from the docs and error messages,** with no code to block them (see "Passwords").
- **SQLite stays the default registry;** the docs lead with Postgres. Without a `[registry]` section, Prumi uses SQLite in `data_dir`, as today, so a `uv` user on a laptop needs no database server. The compose install uses Postgres, and the docs present it as the recommended setup for a real install.
- **The backend is chosen in configuration.**
- **The same numbered, plain-SQL migrations and `schema_version` table on both backends.**
- **The registry is always its own database, never the warehouse:** Prumi's warehouse role is read-only.
- **No SQLite to Postgres migration.**
- **An ADR that supersedes ADR 0013.**
- **The maintainer lets the agent squash-merge these PRs** once CI is green.

## PR 1: settings and passwords (#30)

### Settings from environment variables

- **Names:** `PRUMI__` plus the section path and the key, upper case, joined by double underscores. *My call* (the convention pydantic-settings and Airflow use):

  | `prumi.toml` | Variable |
  |---|---|
  | `[server] port` | `PRUMI__SERVER__PORT` |
  | `[source] adapter` | `PRUMI__SOURCE__ADAPTER` |
  | `[source.dbt] semantic_manifest` | `PRUMI__SOURCE__DBT__SEMANTIC_MANIFEST` |
  | `[source.dbt.connection] host` | `PRUMI__SOURCE__DBT__CONNECTION__HOST` |
  | `[registry.postgres] dbname` (PR 2) | `PRUMI__REGISTRY__POSTGRES__DBNAME` |

- **Only `PRUMI__` (two underscores) means a setting.** The single-underscore names (`PRUMI_CONTAINER`, `PRUMI_REGISTRY_PASSWORD`, `PRUMI_TEST_*`, `PRUMI_REQUIRE_*`) are not settings and are never read as such.
- **The variables are merged into the file's data before it is checked,** at the level of the raw table, so the adapter's own sections get them too and every existing check (unknown keys, the loopback rule, the refused `password` key) applies unchanged. Core doesn't learn the adapter's keys.
- **An unknown `PRUMI__…` variable stops Prumi,** naming the variable, the way an unknown key in the file does. That needs every section to refuse unknown keys. Today `[source.dbt]` itself doesn't (only `[source.dbt.connection]` does), although `docs/configuration.md` already says an unknown key stops the server. *My call:* the dbt adapter refuses unknown keys in `[source.dbt]` too. It's a fix that makes the code match the docs, as 0.2 did for the connection's keys, and there are no installs it could break.
- **Values from a variable are text.** *My call:* core's own settings (`[server]`, `[source]`, `[registry]`) are converted to their types by core's checks, and its list settings (`script_hosts`, `style_hosts`, `font_hosts`) are comma-separated. **An adapter's settings may arrive as text,** and the adapter converts them: the dbt adapter accepts `"5433"` for `port`, the way it accepts `5433` from the file. Core never guesses a type (a database named `2024` stays text). The adapter protocol's README says that adapter settings may arrive as strings.
- **An empty `PRUMI__…` variable counts as unset.** *My call:* so a compose file can pass optional settings through as `${X:-}` without an empty port being refused as "not an integer".
- **Relative paths** from a variable resolve against the config file's folder when there is a file, and the current directory when there isn't. *My call.*
- **`prumi.toml` becomes optional:** with the default `--config` (`./prumi.toml`) and no such file, Prumi starts from the variables alone. A `--config` path given explicitly that doesn't exist still stops Prumi. With neither a file nor any `PRUMI__` variable, the message names the path it looked for and says settings can also come from `PRUMI__…` variables.
- **Messages name the variable** when a bad core setting came from one (`PRUMI__SERVER__PORT: …`), not the file. *My call:* an adapter's messages keep naming the key (`[source.dbt.connection] port`); the docs show how a key maps to its variable name, which is mechanical, so the protocol doesn't need to carry where each value came from.
- This works everywhere, not only in the image: a `uv` user can use it too.

### Passwords

- **`password_env`** joins `[source.dbt.connection]` for `postgres`: the name of the variable that holds the password. Prumi reads it at startup and hands the password to the driver directly.
- **Named but missing or empty:** Prumi stops at startup with a message naming the variable, never a value.
- **Not set:** Prumi connects without a password. A server that needs none (local trust or peer authentication, certificates) works as before.
- **Libpq's own lookup:** when Prumi passes no password, libpq still looks in `PGPASSWORD` and `~/.pgpass` by itself. Blocking that takes code and would only hurt people who set those up on purpose, so nothing blocks it; it's just no longer documented or suggested. When Prumi passes a password, libpq uses that one.
- **The `password` key stays refused,** with the message pointing to `password_env`.
- **The connection-failure messages** that today say "the password's source" or name `PGPASSWORD` / pgpass point to `password_env` instead.
- **Tests:** the existing sentinel tests (a sentinel password never in any log record or message) cover `password_env`, and a named-but-missing variable, and a password that came from a variable.
- **A Proposed ADR superseding 0016:** Prumi keeps its own connection, the password never goes in `prumi.toml`, and it comes from a variable `prumi.toml` names.

### Docs (PR 1)

- `docs/configuration.md`: a section on settings from environment variables (names, precedence, lists, an optional `prumi.toml`), and `password_env` in place of the libpq paragraph.
- `packages/prumi-adapter-dbt/README.md`: the Postgres connection's password, and (the adapter protocol) that settings may arrive as text.
- `README.md`: the configure step's example, and the troubleshooting line on Postgres startup messages.
- The ADR.

## PR 2: the registry in Postgres (#28)

### Configuration

```toml
[registry]
backend = "postgres"   # default "sqlite": registry.sqlite3 in data_dir, as today

[registry.postgres]
host = "registry-db"
port = 5432            # optional
dbname = "prumi"
user = "prumi"
sslmode = "require"    # optional
```

- *My call:* the key names follow `[source.dbt.connection]` for Postgres. `[registry.postgres]` is refused when `backend` is `sqlite`, and required when it is `postgres`. Unknown keys and a `password` key are refused, as for the warehouse.
- **The password** comes from `PRUMI_REGISTRY_PASSWORD`. Unset, Prumi connects without one; if the server then refuses the login, the message says to set `PRUMI_REGISTRY_PASSWORD`.
- **Never the warehouse:** *my call:* the registry checks the database it's given, not the warehouse's settings. On first use (no `schema_version` table yet), if the database already holds any table outside the system schemas, Prumi refuses to start and says the registry needs a database of its own. That catches the warehouse whatever name or address reaches it (`localhost` vs `127.0.0.1`, a service name vs its IP), needs nothing from the adapter, and doesn't change the adapter protocol. Once the registry's own tables exist, the check doesn't run again.
- **Startup checks** like the warehouse's: can't connect, can't log in, database doesn't exist, each with its own message naming the settings to check, built from Prumi's own text, never the driver's message or a password. The role must be able to create tables (the migrations run at startup); if it can't, the message says so.
- **The driver:** *my call:* psycopg becomes an optional extra of core, `prumi[postgres]`. `prumi[dbt]` already brings it, and the image has it. With `backend = "postgres"` and no psycopg, Prumi stops with a message naming the extra.

### The registry on two backends

- **The same behavior:** the `Registry`'s public methods, and what `prumi list`, `certify`, `uncertify`, `delete` and `requests` print, are the same on both. The CLI commands and the server use whichever backend the config names.
- **Migrations:** the same numbers and the same `schema_version` table on both, run at startup in a transaction each. Where the SQL can't be the same (SQLite's `INTEGER PRIMARY KEY AUTOINCREMENT`, Postgres's identity columns, say), each backend has its own file for that number, and the resulting tables, columns and constraints mean the same. *My call.* An existing 0.2 SQLite registry must open unchanged: its applied migrations aren't run again, and a rewrite of an existing SQLite migration file must not change what it creates.
- **Text stays text.** Timestamps, spec JSON and HTML are stored the way SQLite stores them today (ISO text, the canonical JSON, the normalized HTML), so fingerprints and hashes are computed from the same bytes on both. *My call:* no switch to native Postgres types in this PR.
- **Transactions:** publishing, certifying and deleting stay single transactions that change several tables at once. SQLite's `BEGIN IMMEDIATE` has a Postgres counterpart that gives the same result for one process; the builder picks it.
- **SQLite-only behavior stays SQLite-only:** WAL mode and the local-disk warning apply to SQLite.

### Tests

- **The registry's tests run on both backends** (one parametrized suite, *my call*). On Postgres each test gets an empty database or schema. They use the existing test Postgres (`PRUMI_TEST_POSTGRES_*`), skip without one locally, and must run in CI (`PRUMI_REQUIRE_POSTGRES=1`).
- **The same results on both:** a test publishes the same spec and HTML on both backends and compares fingerprints, hashes and statuses.
- **A 0.2 registry still opens:** a test builds a registry with the 0.2 migrations, then opens it with the new code.
- **Startup:** the refusals above (a database that already holds other tables, a missing extra, a role that can't log in or create tables), with the sentinel check for `PRUMI_REGISTRY_PASSWORD`.
- **The server and the CLI on Postgres:** at least one test starts the server on the Postgres registry, publishes and certifies through the CLI, and sees the status on the next request.

### Docs (PR 2)

- `docs/configuration.md`: `[registry]` and `[registry.postgres]`, `PRUMI_REGISTRY_PASSWORD`, and backing up each backend (the SQLite note stays; `pg_dump` for Postgres). Postgres is presented as the recommended registry for a real install, SQLite as the default for trying things on one machine.
- `README.md`: known limitations ("one machine" wording, where the registry lives).
- **A Proposed ADR superseding 0013:** the registry is SQLite or Postgres, chosen in configuration; the shared migrations; always its own database; the password's variable.
- `CONTRIBUTING.md`, if running the registry tests on Postgres needs anything beyond today's Postgres-test setup.

## Left to the builder

- How the variables are merged into the config table, and how the messages point to a variable.
- How the registry is split between the two backends (one class over a small connection layer, or two classes), and how `?` and `%s` placeholders are handled.
- The Postgres counterpart of `BEGIN IMMEDIATE`, and connection timeouts.
- How each Postgres test gets an empty database or schema.
