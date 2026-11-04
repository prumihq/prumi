# Configuration

Prumi reads its settings from a file, `prumi.toml`, from `PRUMI__…` environment variables, or from both: see [Settings from environment variables](#settings-from-environment-variables). The file is passed with `--config` (`prumi serve --config prumi.toml`); without `--config`, Prumi reads `./prumi.toml` if it exists. Relative paths in the file resolve against the folder the file is in. An unknown section or key stops the server with a message that names it.

```toml
[server]
locale = "en-US"

[source]
adapter = "dbt"
project = "analytics"

[source.dbt]
semantic_manifest = "my-dbt-project/target/semantic_manifest.json"

[source.dbt.connection]
type = "duckdb"
path = "warehouse.duckdb"
```

The [demo's config](../examples/demo-dbt-project/prumi.toml) is a working example, but it pins `as_of` to a date: leave that out for your own data.

## `[server]`

Every key is optional.

| Key | Default | What it does |
|---|---|---|
| `host` | `127.0.0.1`, or `0.0.0.0` in the container image | The address Prumi listens on. Outside a container it must be a loopback address (`127.0.0.1`, `::1` or `localhost`): this release has no login, so anything else would expose the warehouse to the network. In the container image any address is allowed, and publishing with `-p 127.0.0.1:…` keeps Prumi off the network; see [Running in a container](#running-in-a-container). |
| `port` | `8484` | The port. The MCP endpoint is `http://<host>:<port>/mcp`. In the container images, leave it at `8484` and change the host side of `-p` instead. |
| `data_dir` | `.prumi` | Where the SQLite registry (`registry.sqlite3`) is kept, the default; see [`[registry]`](#registry). Use a local disk: SQLite's WAL mode doesn't work over a network filesystem. A Postgres registry doesn't use it. |
| `locale` | `en-US` | The display locale, a tag such as `en-US`, `pt-BR` or `de-DE`. Every viewer sees numbers and dates formatted this way, whatever their browser's language. |
| `as_of` | today | Pins "today" to a date (`2026-10-15`), so time windows and partial periods are computed as of that day. For demos and tests. |
| `script_hosts` | `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `unpkg.com` | The hosts a dashboard may load scripts from. Validation warns about a script URL with no exact version. |
| `style_hosts` | the same, plus `fonts.googleapis.com` | The hosts a dashboard may load stylesheets from. |
| `font_hosts` | the same, plus `fonts.gstatic.com` | The hosts a dashboard may load fonts from. |

## `[source]`

| Key | Default | What it does |
|---|---|---|
| `adapter` | required | The adapter that reads your metrics. This release has one: `dbt`. |
| `project` | none | A name for people: the home page shows it in its header. |

The adapter's own settings go in `[source.<adapter>]`.

## `[source.dbt]`

| Key | What it does |
|---|---|
| `semantic_manifest` | The path to your dbt project's `target/semantic_manifest.json`, which `dbt parse` writes. Prumi reads only this file, and reloads it when it changes; see [Preparing your dbt project](../packages/prumi-adapter-dbt/README.md#preparing-your-dbt-project). |

### `[source.dbt.connection]`

| Key | What it does |
|---|---|
| `type` | The warehouse type: `duckdb` or `postgres`. |

For `duckdb`:

| Key | What it does |
|---|---|
| `path` | The DuckDB file dbt built. Prumi opens it read-only. |

For `postgres`:

| Key | What it does |
|---|---|
| `host` | Required. The Postgres server: a host name, not a connection string. |
| `dbname` | Required. The database dbt built: its name, not a connection string. |
| `user` | Required. The role Prumi connects as. |
| `password_env` | The name of the environment variable that holds the role's password, such as `WAREHOUSE_PASSWORD`. Unset, Prumi connects without a password. |
| `port` | An integer. Default `5432`. |
| `sslmode` | One of `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`. Unset, libpq decides. |
| `timezone` | A time zone name (such as `America/Sao_Paulo`) for the session. Unset, the connection keeps its own time zone, from the server, database or role setting, or `PGTZ`. Set, Prumi refuses to start if `PGTZ` overrides it. |

There is no `password` key, and Prumi refuses one: the password never goes in `prumi.toml`. Put it in an environment variable and name that variable in `password_env`:

```toml
[source.dbt.connection]
type = "postgres"
host = "db.internal"
dbname = "analytics"
user = "prumi_reader"
password_env = "WAREHOUSE_PASSWORD"
```

Prumi reads the variable at startup and hands the password to the driver. If the variable is unset, empty or only whitespace, Prumi stops at startup and says so; no message or log ever shows the password. Without `password_env`, Prumi connects without a password, for a server that doesn't ask for one (trust or peer authentication, client certificates). If the server refuses the login, the message points to `user` and `password_env`.

Every Postgres session is read-only (`default_transaction_read_only = on`), so nothing Prumi runs can write, and prints dates in ISO format (`DateStyle = ISO`), whatever the role or database sets. Any other key is refused by name.

Changes to the config take effect when Prumi restarts. The server reloads the manifest when it changes: a new one that fails a check is logged and the current one stays in use.

## `[registry]`

The registry holds what Prumi stores: dashboards and their versions, certifications, metric requests and the check reports from viewers' browsers. It lives in a Postgres database or in a SQLite file.

| Key | Default | What it does |
|---|---|---|
| `backend` | `sqlite` | `postgres` or `sqlite`. |

- **Postgres** is the registry for a real install: a database of its own, on a server you already run and back up. Its settings go in [`[registry.postgres]`](#registrypostgres).
- **SQLite**, the default, needs nothing set up: the registry is the file `registry.sqlite3` in `data_dir`, on one machine. Without a `[registry]` section, that's the registry.

Changing `backend` starts with an empty registry: nothing is copied from the other one.

### `[registry.postgres]`

Required with `backend = "postgres"`, and refused with `sqlite`.

| Key | What it does |
|---|---|
| `host` | Required. The Postgres server: a host name, not a connection string. |
| `dbname` | Required. The registry's database: its name, not a connection string. It must be a database of its own; see below. |
| `user` | Required. The role Prumi connects as. It must be able to create tables in the database: the registry creates its own at startup. |
| `port` | An integer. Default `5432`. |
| `sslmode` | One of `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`. Unset, libpq decides. |

Any other key is refused by name, `password` included. The password comes from the environment variable `PRUMI_REGISTRY_PASSWORD`, always under that name; unset or empty, Prumi connects without a password. It's not read with `backend = "sqlite"`. No message or log ever shows it.

```toml
[registry]
backend = "postgres"

[registry.postgres]
host = "db.internal"
dbname = "prumi_registry"
user = "prumi"
sslmode = "require"
```

```sh
export PRUMI_REGISTRY_PASSWORD=...   # from wherever you keep secrets
prumi serve
```

Prumi needs the psycopg driver for a Postgres registry: `prumi[dbt]` brings it, and so does the container image. Without them, install the extra `prumi[postgres]`.

**A database of its own.** Create an empty database for the registry, in UTF8, owned by the role Prumi connects as, so that role can create the registry's tables:

```sql
CREATE ROLE prumi LOGIN PASSWORD '...';
CREATE DATABASE prumi_registry OWNER prumi ENCODING 'UTF8' TEMPLATE template0;
```

The first time Prumi opens it, before the registry's tables exist, Prumi refuses a database that already holds a table or view (in any schema but Postgres's own) and names what it found, and refuses one whose encoding isn't UTF8: the registry sorts text by its bytes, which gives SQLite's order only in UTF-8. Never point the registry at the warehouse, even in a schema of its own: Prumi's warehouse role is read-only and stays that way, and the registry's tables don't belong among the ones dbt builds. Once the registry's tables exist, the check doesn't run again. An extension that creates tables in a new database (PostGIS's `spatial_ref_sys`, say, when the server's `template1` has it) makes it count as not empty: use a database without such extensions.

**At startup** Prumi opens the registry before it starts listening, so a registry it can't use stops it with a message naming what to change: `host`, `port` or `sslmode` when the server can't be reached, `user` and `PRUMI_REGISTRY_PASSWORD` when the login is refused, `dbname` when the database doesn't exist, the `GRANT CONNECT` to give when the role may not connect to it, the role's privileges when it can't create tables, and `prumi[postgres]` when the driver is missing. The CLI commands stop with the same messages, as `prumi: ...`, without a traceback.

**One registry per deployment.** One `prumi serve` uses a registry, and the CLI commands run with the same settings use it beside the server. Don't point two Prumi deployments at one registry database, and don't run several servers on one: each server keeps the candidates `validate_spec` checked in its own memory, so another server couldn't publish them. Writes to the registry take turns, as on SQLite: one that waits more than 10 seconds for another to finish fails with a message saying another writer holds the registry.

### Backing up the registry

- **Postgres:** back up the registry's database like any other, with `pg_dump` (`pg_dump -Fc -d prumi_registry -f prumi_registry.dump`) or your server's own backups.
- **SQLite:** stop the server and copy `registry.sqlite3`, or run `sqlite3 registry.sqlite3 ".backup <file>"` while it runs. In WAL mode, recent writes may still be in `registry.sqlite3-wal`, so copying the main file alone can miss them.

## Settings from environment variables

Every setting can also come from an environment variable: `PRUMI__`, then the section's path and the key, in upper case, joined by double underscores.

| `prumi.toml` | Variable |
|---|---|
| `[server] port` | `PRUMI__SERVER__PORT` |
| `[source] adapter` | `PRUMI__SOURCE__ADAPTER` |
| `[source.dbt] semantic_manifest` | `PRUMI__SOURCE__DBT__SEMANTIC_MANIFEST` |
| `[source.dbt.connection] host` | `PRUMI__SOURCE__DBT__CONNECTION__HOST` |
| `[source.dbt.connection] password_env` | `PRUMI__SOURCE__DBT__CONNECTION__PASSWORD_ENV` |
| `[registry] backend` | `PRUMI__REGISTRY__BACKEND` |
| `[registry.postgres] host` | `PRUMI__REGISTRY__POSTGRES__HOST` |

For example, a Postgres setup with no `prumi.toml` at all:

```sh
export PRUMI__SOURCE__ADAPTER=dbt
export PRUMI__SOURCE__DBT__SEMANTIC_MANIFEST=/srv/analytics/target/semantic_manifest.json
export PRUMI__SOURCE__DBT__CONNECTION__TYPE=postgres
export PRUMI__SOURCE__DBT__CONNECTION__HOST=db.internal
export PRUMI__SOURCE__DBT__CONNECTION__DBNAME=analytics
export PRUMI__SOURCE__DBT__CONNECTION__USER=prumi_reader
export PRUMI__SOURCE__DBT__CONNECTION__PASSWORD_ENV=WAREHOUSE_PASSWORD
# WAREHOUSE_PASSWORD itself comes from wherever you keep secrets.
prumi serve
```

- **The environment wins.** A variable overrides the same key in `prumi.toml`; the keys no variable sets keep the file's values.
- **An empty variable counts as unset,** so a compose file can pass an optional setting through as `${X:-}`.
- **Values are text, and Prumi converts them:** `PRUMI__SERVER__PORT=9000` is the port 9000, `PRUMI__SERVER__AS_OF=2026-10-15` a date, and `PRUMI__SOURCE__DBT__CONNECTION__PORT=5433` the Postgres port. Nothing else is guessed: a database named `2024` stays text.
- **Lists are comma-separated:** `PRUMI__SERVER__SCRIPT_HOSTS=cdn.jsdelivr.net,unpkg.com`, and the same for `style_hosts` and `font_hosts`. A variable replaces the whole list; one of only commas and spaces empties it (only an empty variable counts as unset).
- **`prumi.toml` is optional.** Without `--config`, Prumi reads `./prumi.toml` if it exists, and otherwise starts from the variables alone. A file named with `--config` must exist. With neither a file nor any `PRUMI__` variable, Prumi stops with a message naming the file it looked for.
- **Relative paths** in a variable resolve against the config file's folder, or against the current folder when there's no file.
- **Mistakes stop Prumi.** A name that isn't upper-case words joined by double underscores, an unknown section, an unknown key in `[server]`, `[source]` or `[registry]`, or a bad value for a `[server]` or `[registry]` setting gets a message naming the variable (`PRUMI__SERVER__PORT: …`), never its value. The adapter's messages name the key (`[source.dbt.connection] port`), which maps to its variable as in the table above.
- **Only `PRUMI__`, with two underscores, means a setting.** `PRUMI_CONTAINER`, `PRUMI_REGISTRY_PASSWORD` and other names with one underscore are never read as settings.

Every command that reads settings reads them, not only `prumi serve`, in a `uv` install as in a container.

## Running in a container

The images, `ghcr.io/prumihq/prumi` and the demo's `ghcr.io/prumihq/prumi-demo`, set the environment variable `PRUMI_CONTAINER=1`. It changes how Prumi listens, because a container's loopback address is its own: a server that listens only there can't be reached from the host machine, even through a published port.

| | `PRUMI_CONTAINER` unset (a `uv` install) | `PRUMI_CONTAINER=1` (the images) |
|---|---|---|
| Default `host` | `127.0.0.1` | `0.0.0.0` |
| A non-loopback `host` in the config | refused | allowed |
| Host names the server answers | `<host>:<port>` and `localhost:<port>` | `localhost`, `127.0.0.1` and `[::1]`, on any port |
| The links Prumi hands out | built from `host` and `port` | built from the address the request came in on |
| `prumi certify` without `--by` | certifies as the OS user | refused |

Only `1` turns it on: any other value, `0` included, counts as unset. Don't set it outside a container; it's what lets Prumi listen beyond loopback. Inside the image, a config that sets a loopback `host` still starts, with a warning that the server can't be reached from outside the container: leave `host` unset there.

**Keep `127.0.0.1:` in `-p`.** Inside the container Prumi listens on every address, and Docker decides who can reach it. `-p 127.0.0.1:8484:8484` publishes the port on the host machine's loopback address only, so only this machine can open Prumi. `-p 8484:8484` would publish it on every address of the host machine, to anyone on its network, and Docker's port rules can bypass a host firewall such as ufw. Prumi can't see the difference from inside the container, so it can't refuse. This release has no login: anyone who reaches Prumi can open every dashboard and call every MCP tool. [ADR 0017](adr/0017-docker-is-the-gate-in-the-container.md) has the reasoning.

**Any local web page can call the MCP endpoint.** Prumi can't know which port Docker publishes it on, so in a container its MCP endpoint accepts requests from a page served on `localhost`, `127.0.0.1` or `[::1]`, on any port. Another web page running on your machine, such as a dev server, could call Prumi's MCP tools from your browser. Pages from other sites are refused. This ends with login.

**Another port.** The images listen on `8484`, and their healthcheck checks `8484`. To use another port, change the host side of `-p` (`-p 127.0.0.1:9000:8484`), not `port` in the config. The links Prumi hands out then name the port you chose, and the MCP endpoint is `http://127.0.0.1:9000/mcp`.

**Your own project.** The main image reads `/prumi/prumi.toml`, and the `PRUMI__…` variables passed with `-e` or `--env-file` ([Settings from environment variables](#settings-from-environment-variables)). Started with neither, it stops with a message naming that path; it never falls back to the demo. Mount your config there:

```sh
docker run -p 127.0.0.1:8484:8484 -v "$PWD/prumi.toml:/prumi/prumi.toml:ro" \
  -v prumi-data:/prumi/.prumi ghcr.io/prumihq/prumi
```

Any setting can come from a variable instead, or override the file's: `-e PRUMI__SOURCE__DBT__CONNECTION__HOST=db.internal`. A relative path in a variable resolves against `/prumi`. Pass the warehouse password the same way, under the name `password_env` gives: `-e WAREHOUSE_PASSWORD` with no value copies it from your shell, so it stays off the command line.

The `prumi-data` volume holds the SQLite registry (`/prumi/.prumi`: dashboards and certifications), so it survives a new container, such as one from a newer image; without it, they're lost with the container. A [Postgres registry](#registrypostgres) needs no volume: pass its password with `-e PRUMI_REGISTRY_PASSWORD`. Any folder you bind-mount that Prumi writes to must be writable by uid 10001, the user Prumi runs as in the image.

Paths and host names in the config are seen from inside the container. Mount the semantic manifest and a DuckDB file too, at the paths the config names, and remember that `localhost` there is the container itself, not your machine.

**The CLI** runs inside the container with `docker exec`: `docker exec <container> prumi list`, where `<container>` is the name or ID `docker ps` shows. `prumi certify` needs `--by` there, because the OS user inside is the image's, not yours:

```sh
docker exec <container> prumi certify <id> 1 --by "Your Name"
```

**Chromium's own sandbox is off,** in the images as everywhere else in this release: Playwright starts Chromium without it unless told otherwise. The dashboard's sandboxed frame ([ADR 0004](adr/0004-sandboxed-frame.md)) is a different thing and stays on. Turning Chromium's sandbox on is [#31](https://github.com/prumihq/prumi/issues/31). The images run as a non-root user, which limits what a page that broke out of Chromium could reach.

The images are tested with Docker Engine on Linux. They should work with Docker Desktop and Podman, but those aren't tested.
