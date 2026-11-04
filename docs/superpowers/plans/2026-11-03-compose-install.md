# Compose Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `docker-compose.yml` and `.env.example` that run Prumi on a team's own dbt project, with the registry in a bundled Postgres, Prumi on `127.0.0.1` only, and docs that lead with it.

**Architecture:** Two services: `prumi` (the main image, the dbt project folder mounted read-only, settings as `PRUMI__…` variables, the rest from `.env`) and `registry-db` (Postgres on a named volume). The release attaches both files to the GitHub Release, so the docs download them from `releases/latest/download/`. A `localhost` hint in connection errors inside the container, and CI tests for the compose file and for manifest reload through a mount.

**Tech Stack:** Docker Compose v2, Postgres 18, Python 3.12, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-03-compose-install-design.md` (issue #12). Builds on the merged work from `docs/superpowers/plans/2026-11-03-container-image.md`, `…-settings-from-environment.md` and `…-postgres-registry.md`.

## Global Constraints

- Branch `feat/compose-install`, from `main` after the image, settings and registry PRs are merged. PR title `feat: run Prumi on your own dbt project with docker compose`; body `Closes #12`.
- `NEXT.md` is never committed.
- The published port is always `127.0.0.1:${PRUMI_PORT:-8484}:8484`; the address isn't a value anyone sets.
- No password is written in `docker-compose.yml` or `prumi.toml`; passwords come from `.env`.
- The prumi image is pinned to a release with a `# x-release-please-version` marker, and `deploy/compose/docker-compose.yml` is added to `release-please-config.json`'s `extra-files` (type `generic`). Nobody edits the version by hand.
- A team changes values in `.env`, never the compose file's structure.
- The registry database is never the warehouse (enforced by the registry's own check).
- Docs promise "no restart after `dbt parse`" only for Docker Engine on Linux and WSL2 with Docker Engine.
- Comments never cite designs or plans. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- `.env` with Windows line endings (CRLF), as a file edited on Windows would have: compose reads the values without a trailing `\r` reaching Prumi (a password with `\r` fails login). Pinned in Task 4 (a CRLF `.env` in the smoke test).
- A `DBT_PROJECT_DIR` with spaces in it: the mount still works. Pinned in Task 4 (the smoke test's project folder has a space).
- `docker compose up` before `dbt parse` has ever run (no `target/` yet): Prumi stops with the manifest message naming the in-container path, and `restart: unless-stopped` doesn't hide it behind an endless restart loop without the message in `docker compose logs`. Pinned in Task 4 (check the message in the logs).
- A warehouse `host` of `127.0.0.1` (not `localhost`) inside the container: the hint still appears. Pinned in Task 1.
- `docker compose down` then `up`: dashboards survive (the volume). Pinned in Task 4.

---

### Task 1: The `localhost` hint inside the container

**Files:**
- Modify: `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/connections/postgres.py` (`connect_error_message`)
- Modify: `packages/prumi-core/src/prumi/registry/postgres_errors.py` (`connect_message`)
- Test: `packages/prumi-adapter-dbt/tests/test_postgres_settings.py`, `packages/prumi-core/tests/test_registry_postgres.py`

**Interfaces:**
- Produces: `LOCALHOST_IN_CONTAINER_HINT = "Inside a container, localhost is the container itself: a server on the host machine is host.docker.internal (on Linux, add extra_hosts: host.docker.internal:host-gateway)."` — the adapter has its own copy and reads `os.environ.get("PRUMI_CONTAINER") == "1"` itself (the adapter protocol doesn't change; core's `in_container()` stays core's).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_in_a_container_a_failed_loopback_connection_points_to_host_docker_internal(monkeypatch, host):
    # PRUMI_CONTAINER=1, a connection-refused error -> message ends with the hint

def test_outside_a_container_there_is_no_hint(monkeypatch): ...
def test_in_a_container_other_hosts_get_no_hint(monkeypatch): ...      # host "db.internal"
def test_a_login_failure_gets_no_hint(monkeypatch): ...                # the server answered
```
The same four for the registry's `connect_message`.

- [ ] **Step 2: Run them to see them fail**; Expected: FAIL.
- [ ] **Step 3: Implement** — append the hint only for the general "can't connect" branch, when the host is loopback and the variable is `1`.
- [ ] **Step 4: Run** both packages' suites; Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add packages/prumi-adapter-dbt packages/prumi-core/src/prumi/registry/postgres_errors.py packages/prumi-core/tests/test_registry_postgres.py
git commit -m "Point to host.docker.internal when localhost fails inside a container"
```

### Task 2: The compose file and `.env.example`

**Files:**
- Create: `deploy/compose/docker-compose.yml`, `deploy/compose/.env.example`
- Modify: `release-please-config.json` (`extra-files`)

**Interfaces:**
- Produces (Tasks 3–4 rely on these names): services `prumi` and `registry-db`; volume `registry-data`; in-container paths `/project` (the dbt project, read-only) and manifest `PRUMI__SOURCE__DBT__SEMANTIC_MANIFEST=/project/target/semantic_manifest.json`; `.env` names `DBT_PROJECT_DIR`, `PRUMI_PORT`, `WAREHOUSE_PASSWORD`, `REGISTRY_PASSWORD`, plus the warehouse `PRUMI__SOURCE__DBT__CONNECTION__HOST` / `__DBNAME` / `__USER` lines and `PRUMI__SOURCE__DBT__CONNECTION__PASSWORD_ENV=WAREHOUSE_PASSWORD`; optional lines commented out: `__PORT`, `__SSLMODE`, `__TIMEZONE`, `PRUMI__SERVER__LOCALE`, the three host lists, `PRUMI__SOURCE__PROJECT`.

`prumi` service: `image: ghcr.io/prumihq/prumi:<current version> # x-release-please-version`; `ports: ["127.0.0.1:${PRUMI_PORT:-8484}:8484"]` with a comment (Prumi has no login yet: the 127.0.0.1 keeps it on this machine, see docs/configuration.md); `volumes: ["${DBT_PROJECT_DIR:?set DBT_PROJECT_DIR in .env}:/project:ro"]`; `env_file: .env`; `environment:` the fixed settings — `PRUMI__SOURCE__ADAPTER: dbt`, the manifest path, `PRUMI__SOURCE__DBT__CONNECTION__TYPE: postgres`, `PRUMI__REGISTRY__BACKEND: postgres`, `PRUMI__REGISTRY__POSTGRES__HOST: registry-db`, `…__DBNAME: prumi`, `…__USER: prumi`, `PRUMI_REGISTRY_PASSWORD: ${REGISTRY_PASSWORD:?…}`; `extra_hosts: ["host.docker.internal:host-gateway"]`; `depends_on: {registry-db: {condition: service_healthy}}`; `restart: unless-stopped`; a commented-out alternative mounting `./prumi.toml:/prumi/prumi.toml:ro`.
`registry-db`: `image: postgres:18`; `environment: {POSTGRES_DB: prumi, POSTGRES_USER: prumi, POSTGRES_PASSWORD: ${REGISTRY_PASSWORD:?…}}`; `volumes: ["registry-data:/var/lib/postgresql"]` (the path the postgres:18 image uses for its data); healthcheck `pg_isready -U prumi -d prumi`; no `ports`.

- [ ] **Step 1: Write both files.** `.env.example` comments say `.env` holds passwords and stays out of version control.
- [ ] **Step 2: Check the file parses with an example `.env`**

Run: `cd deploy/compose && cp .env.example /tmp/x.env && sed -i 's/^DBT_PROJECT_DIR=.*/DBT_PROJECT_DIR=\/tmp/' /tmp/x.env && docker compose --env-file /tmp/x.env config >/dev/null && echo ok` (fill the required values in the copy first)
Expected: `ok`; and `docker compose … config | grep -n "127.0.0.1"` shows the port line.

- [ ] **Step 3: Add the file to release-please** — append `{"type": "generic", "path": "deploy/compose/docker-compose.yml"}` to `extra-files`.
- [ ] **Step 4: Commit**

```bash
git add deploy/compose/docker-compose.yml deploy/compose/.env.example release-please-config.json
git commit -m "Add the docker compose install"
```

### Task 3: Release assets

**Files:**
- Modify: `.github/workflows/release.yml` (job `compose-assets` before `publish-release`)

- [ ] **Step 1:** A job `compose-assets` (`needs: images`, `if: push`, `permissions: contents: write`) that checks out the tag and runs `gh release upload "$TAG" deploy/compose/docker-compose.yml deploy/compose/.env.example --clobber` on the draft release; `publish-release` gains it in `needs`. Comment: the files are attached before publishing so the release never points to a file or image that isn't there.
- [ ] **Step 2: Lint** — same command as the image plan's Task 6 Step 3; Expected: no errors.
- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "Attach the compose files to each release"
```

### Task 4: The compose and reload smoke tests

**Files:**
- Create: `scripts/check_compose.py`
- Modify: `.github/workflows/ci.yml` (job `compose`: needs the CI Postgres service, the locally built image)

**Interfaces:**
- Consumes: Task 2's names; `scripts/check_images.py`'s helpers style; the Postgres demo build (`build.py --target postgres`).
- Produces: `uv run python scripts/check_compose.py --image <ref> --warehouse-host <host> --warehouse-port <port>`.

Checks, in order (each container, volume and temp folder removed at the end, also on failure):

1. Copy the demo project's `target-postgres/` into a temp folder **whose name has a space**, as `<tmp>/my project/target/`.
2. Write a `.env` **with CRLF line endings** pointing at it and at the CI warehouse (the demo's `prumi_reader` role, its password in `WAREHOUSE_PASSWORD`), `REGISTRY_PASSWORD` a random value.
3. Run the shipped `deploy/compose/docker-compose.yml` unchanged, with a generated override file that only replaces the `prumi` image with `--image` and adds the warehouse's host mapping if needed: `docker compose -f deploy/compose/docker-compose.yml -f <override> --env-file <.env> -p prumi-check up -d`.
4. `GET http://127.0.0.1:8484/` -> 200; publish a demo dashboard over MCP; `docker compose exec prumi prumi certify <id> 1 --by "Smoke Test"`; the status API says `certified`.
5. In `registry-db`, `psql -U prumi -d prumi -c "select count(*) from dashboards"` -> 1; on the warehouse, `to_regclass('public.dashboards')` and `to_regclass('main.dashboards')` are NULL.
6. Reload: rewrite `target/semantic_manifest.json` on the host with a changed metric description; within 15 s the catalog served over MCP shows it. Then `rm -rf target && mkdir target` and write a manifest with another description; within 15 s it shows too.
7. `docker compose down` (keeping volumes) and `up -d`: the dashboard is still listed.
8. `docker compose down -v`; a fresh run with `target/` missing: `docker compose logs prumi` contains `semantic_manifest.json` and `/project/target`.

- [ ] **Step 1: Run it locally** against the image built from `main` (`docker build -f deploy/image/Dockerfile --build-arg PRUMI_WHEELS=dist -t prumi:dev .`) and a local Postgres with the demo built on it (`PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest packages/prumi-core/tests/test_postgres_demo.py -q` builds it).
Expected: `check_compose: passed in N s`.

- [ ] **Step 2: The WSL2 run by hand** — on this machine, put the project folder under `/mnt/c/Users/Public/prumi-check/my project/` (a Windows folder) and run the script with `--project-root` pointing there (add that option, default a temp folder). Record the result (pass/fail, time to reload) for the PR description.

- [ ] **Step 3: CI job `compose`** — `services: postgres` as in the `python` job; build the wheels and the main image (`--load`), build the Postgres demo (`uv run --project examples/demo-dbt-project python examples/demo-dbt-project/build.py --target postgres` with the `DEMO_POSTGRES_*` variables), grant the reader as `conftest._grant_reader` does (a few `psql` lines), run `scripts/check_compose.py --warehouse-host host.docker.internal --warehouse-port 5432`.

- [ ] **Step 4: Commit**

```bash
git add scripts/check_compose.py .github/workflows/ci.yml
git commit -m "Smoke-test the compose install and manifest reload through a mount"
```

### Task 5: Docs

**Files:**
- Create: `docs/install.md`
- Modify: `README.md`, `docs/configuration.md`, `CONTRIBUTING.md`, `examples/demo-dbt-project/README.md` (only if its link target moves)

- [ ] **Step 1: `docs/install.md`**: the compose install step by step (download with `curl -LO https://github.com/prumihq/prumi/releases/latest/download/docker-compose.yml` and the same for `.env.example`, `cp .env.example .env`, fill it, `dbt parse`, the read-only warehouse role's `GRANT`s, `docker compose up -d`, connect the agent); the steps table from the spec (uv vs container); a registry on a Postgres the team already runs (change the `PRUMI__REGISTRY__POSTGRES__*` values, remove `registry-db` and `depends_on`; it must be its own database); DuckDB warehouses (the two settings, mount the folder holding the `.duckdb`, not the file); extending the image (`FROM ghcr.io/prumihq/prumi:<version>`, `USER root`, install, `USER` back); the pitfalls (`127.0.0.1:` in `ports`, `localhost` inside the container, reload: tested on Docker Engine on Linux and WSL2, elsewhere `docker compose restart prumi`); upgrading (change the pinned tag, `docker compose pull && docker compose up -d`); backing up (`docker compose exec registry-db pg_dump -U prumi prumi > backup.sql`); running commands with `docker compose exec prumi prumi …` and why `--by` is required.

- [ ] **Step 2: `README.md`**: "Use it with your metrics" becomes the compose install in short (download, fill `.env`, `docker compose up -d`, link to `docs/install.md`), then "Without a container engine, or for development:" today's `uv tool install` + `prumi.toml` text. Troubleshooting: the `localhost` / `host.docker.internal` point, and "a new manifest doesn't show up in a container". Status and roadmap: the `docker compose` item leaves "Team use"; known limitations reworded if they say "one machine" in a way compose changes.

- [ ] **Step 3: `docs/configuration.md`**: "Running in a container" links to `docs/install.md`. `CONTRIBUTING.md`: `deploy/compose/` in the layout table, and `scripts/check_compose.py`.

- [ ] **Step 4: Run everything** — `uv run pytest -q && uv run ruff check && uv run ruff format --check`; Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/install.md README.md docs/configuration.md CONTRIBUTING.md examples/demo-dbt-project/README.md docs/superpowers/specs/2026-11-03-compose-install-design.md docs/superpowers/plans/2026-11-03-compose-install.md
git commit -m "Lead the install docs with docker compose"
```

### Task 6: PR and merge

- [ ] **Step 1:** Push, open the PR (title from Global Constraints, `Closes #12`, the WSL2 result, docs changed, attribution line).
- [ ] **Step 2:** `gh pr checks --watch`; fix failures on the branch.
- [ ] **Step 3:** Squash-merge once green, switch to `main`, pull.
