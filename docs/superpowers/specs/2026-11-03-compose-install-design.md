# Running Prumi for real with `docker compose`

Status: draft, for the maintainer's review. Issue: #12 (milestone 0.3). Third of three 0.3 designs. It builds on `2026-11-03-container-image-design.md` (#29) and `2026-11-03-settings-and-postgres-registry-design.md` (#30, #28), and starts after both are merged.

## Why

The image (first design) makes the container the main way to run Prumi, and the trial is one `docker run` line. A team running Prumi on its own dbt project needs more than one line: the port, the mounted project, the registry's database, the warehouse settings and two passwords. Those belong in one file the team can review and keep, not in a long `docker run` command. Compose is the right tool for that, even for two containers.

Success: a team with a dbt project on Postgres copies two files, fills in their values, runs `docker compose up`, and connects their agent, with the registry in Postgres and Prumi reachable from their own machine only.

## Goals

1. **A `docker-compose.yml` and a `.env.example`** in the repo, for a team's own dbt project.
2. **The docs lead with it:** the README's "Use it with your metrics" becomes the compose install, with `uv tool install "prumi[dbt]"` as the alternative.
3. **The two container pitfalls are covered:** `localhost` inside the container, and manifest reload through a mounted folder.

## Non-goals

- dbt in the image, or Prumi running dbt. The team runs `dbt parse` wherever it already does; Prumi reads only `target/semantic_manifest.json`.
- A compose file for the demo. The trial is the demo image's one line.
- Several Prumi processes, a load balancer, HTTPS, login. One Prumi, on the host machine's `127.0.0.1`.
- Kubernetes manifests or a Helm chart.
- Testing on Docker Desktop (Mac, Windows) or Podman.
- Moving data from a `uv` install's SQLite registry.

## Anti-goals

The work fails, even if compose starts, if:

- the shipped compose file, or any example in the docs, publishes the port on anything but `127.0.0.1`;
- a password sits in the compose file itself, or in `prumi.toml`;
- the docs promise "no restart after `dbt parse`" somewhere it wasn't tested;
- a team must edit the compose file's structure (not just its values) to use their own project;
- the registry could end up in the warehouse database.

## Constraints

- Everything the first two designs decided: the image's layout and `PRUMI_CONTAINER`, settings as `PRUMI__…` variables, `password_env`, `PRUMI_REGISTRY_PASSWORD`, the Postgres registry and its refusal to share the warehouse's database.
- The port stays 8484, published on `127.0.0.1` only.
- No version numbers changed by hand (AGENTS.md).

## Decided by the maintainer

- **Compose is the install for a team's own project;** `uv` stays the documented alternative.
- **The project is mounted read-only; no dbt in the image.** *My call, refining "mount `target/`":* the mount is the dbt project's folder, and the manifest setting points at `<mount>/target/semantic_manifest.json`. A bind mount follows the folder, not its name: if `dbt clean` (or a deploy) deletes and recreates `target/`, a mount of `target/` itself keeps showing the old, deleted folder, and Prumi would serve a stale manifest without a word. Mounting the folder above it survives that. Prumi still reads only the manifest.
- **The registry runs in its own Postgres container by default,** its data on a volume. A team can point Prumi at a Postgres it already runs (a managed database, say) instead, by changing the connection values and removing the bundled service: a configuration change and a short docs section, no special code.
- **Passwords come from environment variables,** never from `prumi.toml` and never written into the compose file.
- **The port is published on `127.0.0.1` only;** the default stays 8484.
- **`localhost` inside the container is the container itself.** A warehouse on the host machine is `host.docker.internal`, which needs `extra_hosts: host.docker.internal:host-gateway` on Linux. The docs say so, and a connection error to `localhost` from inside the container points there.
- **Manifest reload through a mount is tested,** including a Windows folder mounted into a container under WSL2, and the docs promise "no restart" only where it was tested.
- **The README's "Use it with your metrics" becomes the compose install,** with `uv` as the alternative. Troubleshooting gets the `localhost` point.
- **The docs get a short "extend the image" section** (`FROM ghcr.io/prumihq/prumi`, then install packages), for anything the image doesn't ship.
- **The maintainer lets the agent squash-merge this PR** once CI is green.

## The files

*My call:* in `deploy/compose/`, next to the image's `deploy/` Dockerfiles: `docker-compose.yml` and `.env.example`.

**Where a team gets them** (*my call*): from the GitHub Release. The release workflow attaches both files as release assets before it publishes the release, so the docs can use the stable `https://github.com/prumihq/prumi/releases/latest/download/docker-compose.yml` URL (and the same for `.env.example`), and the file always pins an image that exists. `main`'s copy is the source, not what people download: between releases it can get ahead of the published image.

**`docker-compose.yml`, two services:**

- **`prumi`:**
  - the main image, pinned to a release (`ghcr.io/prumihq/prumi:<version>`). *My call:* the file is added to release-please's `extra-files`, so each release PR moves the pin with the rest; nobody edits it by hand.
  - `ports: ["127.0.0.1:${PRUMI_PORT:-8484}:8484"]`, with a comment on why the `127.0.0.1:` stays. The host port is a value in `.env`; the address isn't.
  - the dbt project's folder mounted **read-only** at a fixed path inside the container, its host path from `.env` (`DBT_PROJECT_DIR`, say).
  - the settings the file needs to work are `PRUMI__…` variables in the file: the manifest's path inside the container, the warehouse connection's `type`, and the registry (`backend = "postgres"` and the bundled service's values).
  - **every other setting comes from `.env`** (`env_file: .env`): the warehouse's `host`, `dbname`, `user`, `password_env`, and any optional setting (`port`, `sslmode`, `timezone`, `locale`, the allowed CDN hosts) as a `PRUMI__…` line. `.env.example` lists the optional ones commented out. So a team changes values, never the file's structure. *My call.*
  - the passwords passed through from `.env`: the warehouse's (under the name `password_env` gives) and `PRUMI_REGISTRY_PASSWORD`.
  - `extra_hosts: ["host.docker.internal:host-gateway"]`.
  - starts after the registry is healthy (`depends_on` with `condition: service_healthy`).
  - `restart: unless-stopped`. *My call.*
  - a mounted `prumi.toml` is shown as a commented-out alternative.
- **`registry-db`:** the official `postgres` image pinned to a major version (the one CI uses), a named volume for its data, a `pg_isready` healthcheck, and `POSTGRES_PASSWORD` from the **same** `.env` line as `PRUMI_REGISTRY_PASSWORD`. It publishes no port.

**`.env.example`:** the values a team fills in, each with a one-line comment: the path to the dbt project, the warehouse's host, database, user and password, the registry's password, the host port, and the optional settings commented out. The docs say `.env` holds passwords and stays out of version control.

**DuckDB warehouses:** the compose file is written for Postgres. The docs show what changes for DuckDB: the connection's `type` and `path`, and mounting the **folder** that holds the `.duckdb` file read-only (not the file: a build that writes a new file and renames it over the old one, as the demo's `build.py` does, would leave a file mount showing the old data). The builder checks that a DuckDB file opens read-only from a read-only mount.

## The `localhost` pitfall

- When `PRUMI_CONTAINER=1` and the warehouse connection's `host` is `localhost` or a loopback address, and connecting fails, the startup message adds that inside a container `localhost` is the container itself, and that a warehouse on the host machine is `host.docker.internal` (with `extra_hosts` on Linux). Same for the registry's `host`.
- The hint appears only in that case: not outside a container, and not for other hosts.
- *My call:* the warehouse's hint lives in the dbt adapter's connection errors, which already build their own messages; it reads `PRUMI_CONTAINER` itself rather than core passing it in, so the adapter protocol doesn't change.
- Tests cover the hint's presence and absence.

## Manifest reload through a mount

The watcher polls the manifest every two seconds and compares content hashes (`reload.py`), so it doesn't depend on the filesystem's change notifications. The tests confirm it through real mounts:

- **In CI:** a test runs the image with the project folder bind-mounted read-only, rewrites the manifest on the host (as `dbt parse` does), and sees the server serve the new one without a restart. A second case deletes and recreates `target/` (as `dbt clean && dbt parse` does) and sees the new manifest too.
- **By hand, once, before merge:** the same with a folder under `/mnt/c` (a Windows folder) mounted into a container on Docker Engine inside WSL2. The result goes in the PR description.
- **The docs promise "no restart"** for Docker Engine on Linux and for WSL2 with Docker Engine, and say elsewhere (Docker Desktop on Mac or Windows) that if a new manifest doesn't show up within a few seconds, `docker compose restart prumi` loads it.

## Running Prumi's commands

`list`, `certify`, `uncertify`, `delete` and `requests` run inside the running container: `docker compose exec prumi prumi certify <id> 1 --by "Name"`. They use the same environment, so the same registry. The docs show this, and why `--by` is required in a container (first design).

## Tests

- **A CI smoke test of the shipped compose file:** build the demo on the CI Postgres (`build.py --target postgres`), start the shipped `docker-compose.yml` with a test `.env` and an override that points it at the CI warehouse and the locally built image (the file itself is used as shipped, not a copy), and check that the home page answers on `127.0.0.1:8484`, a demo dashboard publishes and validates, `docker compose exec prumi prumi certify … --by …` certifies it, and the registry's tables are in the bundled Postgres, not the warehouse. *My call* on the override's shape.
- The reload test and the `localhost` hint tests above.
- `scripts/check_install.py` keeps testing the `uv` install unchanged.

## Docs

- **`README.md`:**
  - "Use it with your metrics" becomes the compose install: copy the two files, fill `.env`, `dbt parse`, a read-only warehouse role, `docker compose up`, connect the agent. The `uv` install follows as the alternative ("without a container engine, or for development").
  - Troubleshooting: the `localhost` / `host.docker.internal` point; a new manifest that doesn't show up in a container.
  - Status and roadmap: the `docker compose` item leaves the roadmap; known limitations updated.
- **A new `docs/install.md`** (*my call*), linked from the README:
  - the compose install step by step, with the 0.2 vs 0.3 steps table below;
  - using a Postgres the team already runs for the registry (change the values, remove `registry-db`), and that it must be its own database;
  - DuckDB warehouses;
  - extending the image (`FROM ghcr.io/prumihq/prumi`, then install packages);
  - the pitfalls: `127.0.0.1:` in `ports`, `localhost` inside the container, manifest reload;
  - upgrading (change the pinned tag, `docker compose pull && docker compose up -d`) and backing up the registry (`pg_dump` on `registry-db`);
  - running Prumi's commands with `docker compose exec`.
- **`docs/configuration.md`:** a link to `docs/install.md` from "Running in a container".
- **`CONTRIBUTING.md`:** `deploy/compose/` in the repository layout.
- **`examples/demo-dbt-project/README.md`:** its link to "Use it with your metrics" still lands in the right place.

**The steps to connect Prumi to a dbt project**, for `docs/install.md`:

| Step | `uv` | Container |
|---|---|---|
| Prepare the dbt project (semantic models, metrics, optional `config.meta.prumi`) | same | same |
| `dbt parse` writes `target/semantic_manifest.json` | same | same |
| A read-only Postgres role for Prumi (`CONNECT`, `USAGE`, `SELECT`) | same | same |
| Install | `uv tool install "prumi[dbt]"` + `prumi install-chromium` | copy `docker-compose.yml` and `.env.example` |
| Configure | `prumi.toml` | `.env` values; paths are paths inside the container |
| Password | a variable named by `password_env` | the same, from `.env` |
| Start | `prumi serve --config prumi.toml` | `docker compose up -d` |
| Connect the agent to `http://127.0.0.1:8484/mcp` | same | same |

## Left to the builder

- The names of the `.env` values, and the fixed paths inside the container.
- The CI smoke test's override and how it waits for the services.
- How the reload test rewrites the manifest and waits for the new one.
