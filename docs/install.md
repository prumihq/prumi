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
   curl -LO https://github.com/prumihq/prumi/releases/latest/download/docker-compose.yml
   curl -LO https://github.com/prumihq/prumi/releases/latest/download/.env.example
   ```

   Each release's `docker-compose.yml` names that release's image, so the two always match.

4. **Fill in `.env`.** Copy the example next to `docker-compose.yml` (compose reads it from there) and fill in your values: the path to the dbt project, the warehouse's host, database and role, the role's password, and a password of your choice for the registry.

   ```sh
   cp .env.example .env
   ```

   `.env` holds both passwords: keep it out of version control. Change values in `.env`, not the compose file: everything specific to your project lives there. A warehouse on this same machine is `host.docker.internal`, not `localhost` ([why](#localhost-inside-the-container)). Every other setting can go in `.env` as a `PRUMI__…` line ([Settings from environment variables](configuration.md#settings-from-environment-variables)); `.env.example` lists the optional ones, commented out.

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

This is the one change made in `docker-compose.yml` itself. In the `prumi` service, set the registry's values and remove `depends_on`:

```yaml
      PRUMI__REGISTRY__POSTGRES__HOST: db.internal
      PRUMI__REGISTRY__POSTGRES__DBNAME: prumi_registry
      PRUMI__REGISTRY__POSTGRES__USER: prumi
```

Then remove the `registry-db` service and the `registry-data` volume. `REGISTRY_PASSWORD` in `.env` becomes the password of that role, and optional settings such as `PRUMI__REGISTRY__POSTGRES__SSLMODE=require` or `PRUMI__REGISTRY__POSTGRES__PORT` go in `.env`. Make the same change again when you upgrade with a new `docker-compose.yml`.

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

Switch back to `USER prumi` at the end: Prumi runs as that user (uid 10001), not as root. Build it (`docker build -t my-prumi:<version> .`) and set `image:` in `docker-compose.yml` to it.

## Upgrading

Read the [changelog](../CHANGELOG.md) for the release, then download its `docker-compose.yml` over yours (or change the version in the `image:` line), and run:

```sh
docker compose pull && docker compose up -d
```

`.env` stays as it is, and the registry keeps its data on the `registry-data` volume. Restart your agent sessions afterwards: an open session keeps the tools it loaded when it started.

## Backing up the registry

The registry's data is on the `registry-data` volume. Dump it with `pg_dump` while it runs:

```sh
docker compose exec -T registry-db pg_dump -U prumi prumi > backup.sql
```

`-T` keeps the dump free of a terminal's line endings. `docker compose down` keeps the volume; `docker compose down -v` deletes it, and the registry with it.
