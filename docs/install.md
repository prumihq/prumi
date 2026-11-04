# Installing Prumi with `docker compose`

This is how to run Prumi on your own dbt project: two containers, Prumi and a Postgres database of its own for the registry (dashboards, certifications and metric requests), started from two files you download from each release. Prumi is reachable from your machine only, on `http://127.0.0.1:8484`.

You need Docker Engine with the `docker compose` plugin, a dbt project with semantic models and metrics on Postgres ([DuckDB](#duckdb-warehouses) works too, with one more file), and an AI agent that supports MCP, such as Claude Code. To try Prumi without a project of your own, the [demo](../README.md#try-it) is one `docker run` line.

## Step by step

1. **Prepare the dbt project** ([what Prumi reads, and how to declare display formats](../packages/prumi-adapter-dbt/README.md#preparing-your-dbt-project)) and run `dbt parse`, which writes `target/semantic_manifest.json`. Prumi reads only that file; it doesn't run dbt, and the image doesn't contain it.

2. **Give Prumi a read-only role on the warehouse.** It needs `CONNECT` on the database dbt builds, `USAGE` on the schemas of the tables in the manifest, and `SELECT` on those tables. Every session Prumi opens is read-only too.

   ```sql
   CREATE ROLE prumi_reader LOGIN PASSWORD '...';
   GRANT CONNECT ON DATABASE analytics TO prumi_reader;
   GRANT USAGE ON SCHEMA marts TO prumi_reader;
   GRANT SELECT ON ALL TABLES IN SCHEMA marts TO prumi_reader;
   ```

   dbt drops and recreates tables when it builds them, and a recreated table loses its grants. Keep them with dbt's own [`grants`](https://docs.getdbt.com/reference/resource-configs/grants) config (`+grants: {select: ["prumi_reader"]}` in `dbt_project.yml`), or with `ALTER DEFAULT PRIVILEGES FOR ROLE <dbt's role> IN SCHEMA marts GRANT SELECT ON TABLES TO prumi_reader`.

3. **Download the two files** into a new folder:

   ```sh
   mkdir prumi && cd prumi
   curl -fLO https://github.com/prumihq/prumi/releases/latest/download/docker-compose.yml
   curl -fL -o .env.example https://github.com/prumihq/prumi/releases/latest/download/env.example
   ```

   Each release's `docker-compose.yml` names that release's image, so the two always match. The release offers the example as `env.example`, without the leading period, because GitHub renames a file whose name starts with one; the second line saves it as `.env.example`. The `-f` makes `curl` fail on a missing file instead of saving GitHub's "Not Found" page.

4. **Fill in `.env`.** Copy the example next to `docker-compose.yml` (compose reads it from there) and fill in your values: the path to the dbt project, the warehouse's host, database and role, the role's password, and a password of your choice for the registry.

   ```sh
   cp .env.example .env
   ```

   `.env` holds both passwords: keep it out of version control. Change values in `.env`, not the compose file: everything specific to your project lives there ([what `.env` can't change](#what-env-cant-change)). `DBT_PROJECT_DIR` is an absolute path, or a relative one that starts with `./` or `../` (relative to this folder): a bare name such as `analytics` is taken as the name of a Docker volume, not a folder. The folder must be readable by uid 10001, the user Prumi runs as in the container; a folder other users can read (`chmod -R o+rX`) is. A warehouse on this same machine is `host.docker.internal`, not `localhost` ([why](#localhost-inside-the-container)). Every other setting can go in `.env` as a `PRUMI__…` line ([Settings from environment variables](configuration.md#settings-from-environment-variables)); `.env.example` lists the optional ones, commented out.

5. **Start it:**

   ```sh
   docker compose up -d
   docker compose logs prumi
   ```

   The log ends with `Uvicorn running` once Prumi is up. If Prumi stops at startup, the log names what to fix (a setting, a missing manifest, a role that can't read a table), and compose keeps restarting it until you do: change `.env`, then `docker compose up -d` again. [http://127.0.0.1:8484](http://127.0.0.1:8484) lists every dashboard.

6. **Connect your agent** to `http://127.0.0.1:8484/mcp` (or the `PRUMI_PORT` you chose), then start a new agent session. With Claude Code:

   ```sh
   claude mcp add --scope user --transport http prumi http://127.0.0.1:8484/mcp
   ```

From here, on Docker Engine (Linux, or WSL2), metric changes need no restart: run `dbt parse` and Prumi picks up the new manifest a few seconds later. Elsewhere, `docker compose restart prumi` loads it ([why](#a-new-manifest-after-dbt-parse)). A change to `.env` needs `docker compose up -d`, which recreates the Prumi container.

### The same steps with `uv`

Without a container engine, or to work on Prumi, install it with `uv` instead ([README](../README.md#use-it-with-your-metrics)). The steps compare like this:

| Step | `uv` | Container |
|---|---|---|
| Prepare the dbt project (semantic models, metrics, optional `config.meta.prumi`) | same | same |
| `dbt parse` writes `target/semantic_manifest.json` | same | same |
| A read-only Postgres role for Prumi (`CONNECT`, `USAGE`, `SELECT`) | same | same |
| Install | `uv tool install "prumi[dbt]"` + `prumi install-chromium` | download `docker-compose.yml` and `.env.example` |
| Configure | `prumi.toml` | `.env` values; paths are paths inside the container |
| Password | a variable named by `password_env` | the same, from `.env` |
| Start | `prumi serve --config prumi.toml` | `docker compose up -d` |
| Connect the agent to `http://127.0.0.1:8484/mcp` | same | same |

## What `.env` can't change

The compose file sets these itself, in the `prumi` service's `environment:`, and compose gives those values precedence over `.env`: a line for one of them in `.env` is ignored.

| Setting | Fixed to |
|---|---|
| `PRUMI__SOURCE__ADAPTER` | `dbt` |
| `PRUMI__SOURCE__DBT__SEMANTIC_MANIFEST` | `/project/target/semantic_manifest.json`, in the mounted `DBT_PROJECT_DIR` |
| `PRUMI__SOURCE__DBT__CONNECTION__TYPE` | `postgres` |
| `PRUMI__REGISTRY__BACKEND` | `postgres` |
| `PRUMI__REGISTRY__POSTGRES__HOST`, `__DBNAME`, `__USER` | the bundled `registry-db`: `registry-db`, `prumi`, `prumi` |

To change one, don't edit `docker-compose.yml`: an upgrade downloads a new one over it and the change is lost. Put the change in a `docker-compose.override.yml` next to it instead. Compose reads that file automatically and merges it over the main file, and an upgrade leaves it alone. [DuckDB warehouses](#duckdb-warehouses) and [the registry on a Postgres you already run](#the-registry-on-a-postgres-you-already-run) are the two such changes this page describes.

## Running Prumi's commands

The commands run inside the Prumi container, with the same settings and so the same registry, from the folder that holds `docker-compose.yml`:

```sh
docker compose exec prumi prumi list
docker compose exec prumi prumi certify <id> 1 --by "Your Name"
docker compose exec prumi prumi requests
```

`certify` needs `--by` in a container: the OS user inside is the image's, not yours, so Prumi can't tell who is certifying. `uncertify` and `delete` run the same way.

## The pitfalls

### Keep `127.0.0.1:` in `ports`

The compose file publishes Prumi as `127.0.0.1:${PRUMI_PORT:-8484}:8484`. The `127.0.0.1:` makes Prumi reachable from this machine only. Without it, Docker publishes the port on every address of the machine, to anyone on its network, and its port rules can bypass a host firewall such as ufw. Prumi has no login yet: anyone who reaches it can open every dashboard and call every MCP tool. To use another port, set `PRUMI_PORT` in `.env`; the address isn't a setting. [Running in a container](configuration.md#running-in-a-container) has the details.

### `localhost` inside the container

Inside the container, `localhost` is the container itself, not your machine. A warehouse on the machine that runs Docker is `host.docker.internal`: Docker Desktop knows that name, and on Linux the compose file's `extra_hosts` line maps it to the host. When a connection to `localhost` fails inside a container, Prumi's startup message says so.

On Linux, that server must also accept connections from the containers, which come from Docker's network, not from `127.0.0.1`: Postgres's `listen_addresses` must include an address they reach, and `pg_hba.conf` must allow their addresses.

### A new manifest after `dbt parse`

Prumi checks the manifest every two seconds and loads a new one without a restart. The compose file mounts the dbt project's folder, not `target/`, so this keeps working when `dbt clean` deletes and recreates `target/`. That's tested with Docker Engine on Linux, and on WSL2 with Docker Engine. Elsewhere (Docker Desktop on Mac or Windows, say), if a new manifest doesn't show up within a few seconds, load it with:

```sh
docker compose restart prumi
```

A manifest that fails a check is logged (`docker compose logs prumi`) and the old one stays in use.

## The registry on a Postgres you already run

The registry can live on a Postgres server your team already runs and backs up, such as a managed database, instead of the bundled `registry-db`. It must be a database of its own, never the warehouse: an empty UTF8 database that Prumi's role owns. Prumi refuses anything else the first time it opens it; [`[registry.postgres]`](configuration.md#registrypostgres) says how to create one.

Leave `docker-compose.yml` as it is, and add a file `docker-compose.override.yml` next to it with that server's values:

```yaml
services:
  prumi:
    environment:
      PRUMI__REGISTRY__POSTGRES__HOST: db.internal
      PRUMI__REGISTRY__POSTGRES__DBNAME: prumi_registry
      PRUMI__REGISTRY__POSTGRES__USER: prumi
    depends_on: !reset {}
  registry-db:
    profiles: [bundled-registry]
```

Compose merges it over the main file: the three values replace the bundled registry's, `!reset` drops Prumi's wait for `registry-db`, and the profile keeps `registry-db` from starting (it would start only with `--profile bundled-registry`). An upgrade replaces `docker-compose.yml` and leaves this file as it is, so the registry stays where it is. `!reset` needs Docker Compose 2.24 or later; `docker compose config` shows the merged result.

The three values belong in this file, not in `.env`: the compose file sets them itself, and its values win over `.env` ([what `.env` can't change](#what-env-cant-change)). `REGISTRY_PASSWORD` in `.env` is still the password, now that role's. Settings the compose file doesn't set, such as `PRUMI__REGISTRY__POSTGRES__SSLMODE=require` or `PRUMI__REGISTRY__POSTGRES__PORT`, go in `.env`. Back it up with that server; [Backing up the registry](#backing-up-the-registry) is about the bundled one.

## DuckDB warehouses

The compose file is written for Postgres. For a DuckDB warehouse, add a file `docker-compose.override.yml` next to it; compose reads it automatically and merges it over the main file:

```yaml
services:
  prumi:
    environment:
      PRUMI__SOURCE__DBT__CONNECTION__TYPE: duckdb
      PRUMI__SOURCE__DBT__CONNECTION__PATH: /warehouse/analytics.duckdb
    volumes:
      - "${DUCKDB_DIR:?set DUCKDB_DIR in .env}:/warehouse:ro"
```

In `.env`, set `DUCKDB_DIR` to the folder that holds the `.duckdb` file, and empty the Postgres lines (`HOST`, `DBNAME`, `USER`, `PASSWORD_ENV`): a DuckDB connection takes only `type` and `path`, and refuses the others. Use the file's own name in `PATH`: dbt writes the database's name, which is the file's, into the manifest.

Mount the **folder**, not the file. A build that writes a new file and renames it over the old one would leave a file mount showing the old data. Prumi opens the file read-only, so a read-only mount is enough. If the file is inside the dbt project's folder, it's mounted already: set `PATH` to `/project/<file>.duckdb` and leave the `volumes` lines out.

## Extending the image

The image carries Prumi, the dbt adapter with the DuckDB and Postgres drivers, and Chromium. For anything else, build your own image on top of it, at the same version as your compose file's:

```dockerfile
FROM ghcr.io/prumihq/prumi:<version>
USER root
RUN apt-get update && apt-get install -y --no-install-recommends <package> \
    && rm -rf /var/lib/apt/lists/*
USER prumi
```

Switch back to `USER prumi` at the end: Prumi runs as that user (uid 10001), not as root. Build it (`docker build -t my-prumi:<version> .`) and name it in `docker-compose.override.yml`, so an upgrade's new `docker-compose.yml` doesn't undo it:

```yaml
services:
  prumi:
    image: my-prumi:<version>
```

When you upgrade, rebuild it on the new version and change the version here too.

## Upgrading

Read the [changelog](../CHANGELOG.md) for the release, then download its `docker-compose.yml` over yours (the first `curl` line of [step 3](#step-by-step)), and run:

```sh
docker compose pull && docker compose up -d
```

`.env` and a `docker-compose.override.yml` stay as they are, and the registry keeps its data on the `registry-data` volume. Restart your agent sessions afterwards: an open session keeps the tools it loaded when it started.

## Backing up the registry

The registry's data is on the `registry-data` volume. Dump it with `pg_dump` while it runs:

```sh
docker compose exec -T registry-db pg_dump -U prumi prumi > backup.sql
```

`-T` keeps the dump free of a terminal's line endings. `docker compose down` keeps the volume; `docker compose down -v` deletes it, and the registry with it.

To restore a dump, load it into a new, empty registry before Prumi starts on it (Prumi creates its tables in an empty registry, and the dump's would then collide with them):

```sh
docker compose down -v                    # deletes the current registry
docker compose up -d --wait registry-db   # a new, empty one
docker compose exec -T registry-db psql -U prumi prumi < backup.sql
docker compose up -d
```

The compose file names its project `prumi`, so the volume is `prumi_registry-data` whatever the folder that holds `docker-compose.yml` is called: moving or renaming that folder keeps the registry. To run a second Prumi on the same machine, from another folder, give it a name of its own with `COMPOSE_PROJECT_NAME=prumi-sales` in its `.env` (and another `PRUMI_PORT`); its volume is then `prumi-sales_registry-data`.
