# Container Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `ghcr.io/prumihq/prumi` (Prumi + Chromium, no demo) and `ghcr.io/prumihq/prumi-demo` (the demo shop on top), so `docker run -p 127.0.0.1:8484:8484 ghcr.io/prumihq/prumi-demo` is the whole trial.

**Architecture:** A `PRUMI_CONTAINER=1` switch, set only by the image, lets Prumi listen on `0.0.0.0`, answer loopback `Host` names on any port, and build its links from the request's `Host`. Two Dockerfiles in `deploy/`: the main image installs `prumi[dbt]` (from PyPI, or from local wheels in CI) plus Chromium; the demo image builds the demo with dbt in a throwaway stage and copies only its outputs. A smoke-test script drives both images in CI and in the release workflow, which pushes multi-arch images to GHCR before publishing the GitHub Release.

**Tech Stack:** Python 3.12, Starlette/uvicorn, the `mcp` SDK, Playwright Chromium, Docker Buildx, GitHub Actions, GHCR.

**Spec:** `docs/superpowers/specs/2026-11-03-container-image-design.md` (issue #29)

## Global Constraints

- Branch `feat/container-image`; the PR says `Closes #29`; PR title `feat: run Prumi from a container image, with a demo image to try it`.
- `NEXT.md` is never committed (`git add` paths explicitly, never `git add -A` / `.`).
- The switch is `PRUMI_CONTAINER`; only the exact value `1` turns it on.
- Outside a container, the loopback refusal message and behavior don't change, and the message doesn't mention `PRUMI_CONTAINER`.
- Every `docker run` / `-p` in docs, scripts and workflows publishes on `127.0.0.1:` only.
- Image names `ghcr.io/prumihq/prumi` and `ghcr.io/prumihq/prumi-demo`; tags: the version (e.g. `0.3.0`) and `latest`; platforms `linux/amd64`, `linux/arm64` on release, `linux/amd64` only on PRs.
- Main image: non-root user, working directory `/prumi`, entrypoint `prumi`, default command `serve`; no dbt, no demo data, no uv in the final image.
- Demo image: `FROM` the main image of the same version, registry in `/data` (writable by the image's user), welcome link `https://github.com/prumihq/prumi/blob/main/examples/demo-dbt-project/README.md#try-prumi-on-it` printed once at start.
- Chromium's launch (`playwright.chromium.launch()`) is not changed; no sandbox work (#31).
- No scheduled rebuild (#32).
- Port 8484. No version numbers changed by hand; no tags created.
- Code comments never cite designs or plans; point to ADR 0017 where a pointer helps.
- Workflow actions pinned by commit SHA with a version comment, like the existing workflows.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- `PRUMI_CONTAINER` set to `true`, `yes` or `0`: must behave as unset (loopback only). Pinned in Task 1.
- An IPv6 browser address `http://[::1]:8484/` inside the container: answered, and links say `[::1]:8484`. Pinned in Task 2.
- A `Host` header with no port (`Host: localhost`), or upper case (`LOCALHOST:8484`): in container mode, the lowercase name is accepted; a missing port is accepted the way browsers send port 80. Pinned in Task 2 (`localhost` without port accepted, `evil.example` refused).
- The demo image started with a volume on `/data` created by root (`docker volume` default): the server must still be able to write the registry. Pinned in Task 5's smoke check with `-v`.
- The main image run with a mounted config whose `host = "127.0.0.1"`: starts, logs the warning, and the smoke test sees the warning text. Pinned in Task 1 (warning unit test).

---

### Task 1: The container switch in config

**Files:**
- Create: `packages/prumi-core/src/prumi/container.py`
- Modify: `packages/prumi-core/src/prumi/config.py` (`_Server.host` default and `_loopback` validator, `load_config`)
- Modify: `packages/prumi-core/src/prumi/server/__init__.py` (`serve`: the loopback warning)
- Test: `packages/prumi-core/tests/test_container.py`

**Interfaces:**
- Produces: `prumi.container.in_container() -> bool` (reads `os.environ` each call; `True` only for `"1"`); `prumi.container.ENV = "PRUMI_CONTAINER"`; `prumi.container.LOOPBACK_IN_CONTAINER_WARNING: str`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 1"])
def test_only_1_turns_the_container_mode_on(monkeypatch, value): ...
    # in_container() is False for each; True for "1"

def test_outside_a_container_the_default_host_is_loopback(monkeypatch, tmp_path):
    # PRUMI_CONTAINER unset, MINIMAL config -> server.host == "127.0.0.1"

def test_in_a_container_the_default_host_is_every_address(monkeypatch, tmp_path):
    # PRUMI_CONTAINER=1 -> server.host == "0.0.0.0"

def test_in_a_container_a_non_loopback_host_is_allowed(monkeypatch, tmp_path):
    # host = "0.0.0.0" in the file, PRUMI_CONTAINER=1 -> loads

def test_outside_a_container_a_non_loopback_host_is_still_refused(monkeypatch, tmp_path):
    # host = "0.0.0.0", unset -> ConfigError whose text equals today's message
    # and does not contain "PRUMI_CONTAINER"

def test_in_a_container_a_loopback_host_logs_a_warning(monkeypatch, caplog, ...):
    # serve-time check: a helper `warn_if_unreachable(server: ServerConfig) -> None` in
    # prumi.container logs LOOPBACK_IN_CONTAINER_WARNING on logger "prumi.server" when
    # in_container() and is_loopback(server.host)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/prumi-core/tests/test_container.py -v`
Expected: FAIL (`ModuleNotFoundError: prumi.container`)

- [ ] **Step 3: Implement**

`container.py`: `in_container()`, `warn_if_unreachable(server)`, the warning text: `"host is a loopback address inside a container, so the server can't be reached from outside it; leave host unset in the image (see docs/configuration.md)."`. In `config.py`: `_Server.host` default becomes `Field(default_factory=lambda: "0.0.0.0" if in_container() else "127.0.0.1")`; `_loopback` returns early when `in_container()`. One-line comment there pointing to ADR 0017. `serve()` calls `warn_if_unreachable(server)` before `uvicorn.run`, and since nothing configures `prumi.server` logging, emit it through the same `_reloads_shown`-style stderr handler or print to stderr (builder's choice; the test asserts on the log record).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/prumi-core/tests/test_container.py packages/prumi-core/tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/prumi-core/src/prumi/container.py packages/prumi-core/src/prumi/config.py packages/prumi-core/src/prumi/server/__init__.py packages/prumi-core/tests/test_container.py
git commit -m "Let Prumi listen on every address inside the container image"
```

### Task 2: Host names and links in the container

**Files:**
- Modify: `packages/prumi-core/src/prumi/server/guards.py` (`allowed_hosts`, `HostCheck`)
- Modify: `packages/prumi-core/src/prumi/mcp/__init__.py` (`_dashboard_url`, `mcp_routes`, the `LOCAL_ONLY_NOTE` condition)
- Modify: `packages/prumi-core/src/prumi/server/app.py` (`index`: the MCP URL)
- Test: `packages/prumi-core/tests/test_container_hosts.py`

**Interfaces:**
- Consumes: `in_container()` (Task 1).
- Produces: `prumi.server.guards.LOOPBACK_NAMES = ("localhost", "127.0.0.1", "[::1]")`; `prumi.server.guards.host_allowed(host_header: str, hosts: frozenset[str]) -> bool` (in container mode: the name part, lowercased, is in `LOOPBACK_NAMES`, any port or none; otherwise exact membership as today); `prumi.server.guards.origin(server: ServerConfig, host_header: str | None) -> str` returning `"http://<host>:<port>"` — in container mode built from the (already checked) `Host` header, otherwise from `server.host`/`server.port` exactly as today.

- [ ] **Step 1: Write the failing tests** (use `TestClient(create_app(published), base_url=...)` as `conftest.client` does, with `monkeypatch.setenv("PRUMI_CONTAINER", "1")` set before `create_app`)

```python
@pytest.mark.parametrize("host", ["127.0.0.1:8484", "localhost:8484", "[::1]:8484",
                                  "127.0.0.1:9000", "LOCALHOST:8484", "localhost"])
def test_in_a_container_loopback_names_are_answered_on_any_port(host, ...):
    # GET / with Host: host -> 200

@pytest.mark.parametrize("host", ["evil.example:8484", "0.0.0.0:8484", "192.168.1.5:8484"])
def test_in_a_container_other_host_names_are_refused(host, ...):  # -> 403

def test_outside_a_container_the_host_check_is_unchanged(...):
    # unset: "127.0.0.1:9000" -> 403, "127.0.0.1:8484" -> 200

def test_in_a_container_the_home_page_shows_the_mcp_url_the_browser_used(...):
    # Host: 127.0.0.1:9000 -> page contains "http://127.0.0.1:9000/mcp"

def test_in_a_container_published_links_use_the_agents_address(...):
    # MCP publish_dashboard over HTTP with Host 127.0.0.1:9000 -> url startswith
    # "http://127.0.0.1:9000/d/" and note == "This link only works on this machine."

def test_outside_a_container_published_links_are_unchanged(...):
    # url startswith "http://127.0.0.1:8484/d/"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/prumi-core/tests/test_container_hosts.py -v`
Expected: FAIL (403 on port 9000, wrong URLs)

- [ ] **Step 3: Implement**

`HostCheck.__call__` uses `host_allowed`. `mcp_routes`: in container mode pass the MCP SDK's `TransportSecuritySettings` loopback names with a wildcard port (`"localhost:*"`, `"127.0.0.1:*"`, `"[::1]:*"`, and the origins `http://<same>`); check the installed SDK supports `:*` patterns, and if it doesn't, disable the SDK's own host check in container mode only, since `HostCheck` already runs first on every route. The MCP tools get the request's `Host` from the tool context (`ctx.request_context.request.headers["host"]` in the installed SDK; verify the attribute path) and pass it to `_dashboard_url(services, id, version, host_header)`, which calls `origin(...)`. The note shows when `is_loopback(server.host) or in_container()`. `index` builds the MCP URL with `origin(server, request.headers.get("host"))`.

- [ ] **Step 4: Run the tests and the existing suite**

Run: `uv run pytest packages/prumi-core -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/prumi-core/src/prumi/server/guards.py packages/prumi-core/src/prumi/mcp/__init__.py packages/prumi-core/src/prumi/server/app.py packages/prumi-core/tests/test_container_hosts.py
git commit -m "Answer loopback names on any port inside the container, and link to the address used"
```

### Task 3: `certify` asks for a name in the container

**Files:**
- Modify: `packages/prumi-core/src/prumi/cli.py` (`_certify`)
- Test: `packages/prumi-core/tests/test_cli.py`

**Interfaces:**
- Consumes: `in_container()` (Task 1).

- [ ] **Step 1: Write the failing tests**

```python
def test_in_a_container_certify_needs_by(monkeypatch, ...):
    # PRUMI_CONTAINER=1, `prumi certify <id> 1` -> exit code REFUSED, stderr contains
    # "--by", and the version stays a draft

def test_in_a_container_certify_with_by_certifies(monkeypatch, ...):
    # `--by "Ana"` -> certified_by == "Ana"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/prumi-core/tests/test_cli.py -k container -v`
Expected: FAIL

- [ ] **Step 3: Implement** — in `_certify`, when `args.by is None and in_container()`, raise `Refused("In a container the OS user is the image's, not yours: pass --by with the name to record, e.g. --by \"Ana Lima\".")`. The `--by` help text becomes `"default: the OS user name (required in a container)"`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/prumi-core/tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/prumi-core/src/prumi/cli.py packages/prumi-core/tests/test_cli.py
git commit -m "Require certify --by inside the container"
```

### Task 4: The main image and the demo image

**Files:**
- Create: `deploy/image/Dockerfile`
- Create: `deploy/demo-image/Dockerfile`, `deploy/demo-image/prumi.toml`, `deploy/demo-image/start.sh`
- Create: `.dockerignore` (repo root: `.venv`, `**/__pycache__`, `.git`, `**/.prumi`, `examples/demo-dbt-project/target*`, `examples/demo-dbt-project/demo.duckdb`, `NEXT.md`)

**Interfaces:**
- Produces (used by Tasks 5–6):
  - `deploy/image/Dockerfile`, build context = repo root, build args `PRUMI_VERSION` (install `prumi[dbt]==$PRUMI_VERSION` from PyPI) or `PRUMI_WHEELS=dist` (install both wheels from `dist/` in the context, the way `scripts/check_install.py` pins them, never from PyPI). Sets `ENV PRUMI_CONTAINER=1`, `PLAYWRIGHT_BROWSERS_PATH` to a folder readable by the image user; `WORKDIR /prumi`; `USER` a non-root user (UID 10001); `ENTRYPOINT ["prumi"]`, `CMD ["serve"]`; `EXPOSE 8484`; a `HEALTHCHECK` that GETs `http://127.0.0.1:8484/` with Python's stdlib (no curl needed).
  - `deploy/demo-image/Dockerfile`, build context = repo root, build arg `PRUMI_IMAGE` (the main image reference, e.g. `ghcr.io/prumihq/prumi:0.3.0` or a local tag). Stage 1 (`python:3.12-slim` + uv): `uv run --project examples/demo-dbt-project python examples/demo-dbt-project/build.py`. Final stage `FROM $PRUMI_IMAGE`: copies `demo.duckdb` and `target/semantic_manifest.json` into `/demo` (keeping the file name `demo.duckdb`: the manifest bakes it in), copies `prumi.toml` and `start.sh` (`COPY --chmod=755`) to `/demo`, creates `/data` owned by the image user, `VOLUME /data`, `ENTRYPOINT ["/demo/start.sh"]`.
  - `deploy/demo-image/prumi.toml`: the demo's `[server]` settings without `host` (`port = 8484`, `data_dir = "/data"`, `as_of = "2026-10-15"`, `locale = "en-US"`, the same three host lists as `examples/demo-dbt-project/prumi.toml`), `[source] adapter = "dbt"`, `project = "prumi_demo"`, `[source.dbt] semantic_manifest = "target/semantic_manifest.json"`, `[source.dbt.connection] type = "duckdb"`, `path = "demo.duckdb"`.
  - `start.sh`: prints `Prumi demo: what's in the shop, how to connect your agent and what to ask: https://github.com/prumihq/prumi/blob/main/examples/demo-dbt-project/README.md#try-prumi-on-it`, then `exec prumi serve --config /demo/prumi.toml "$@"`.

- [ ] **Step 1: Build both images locally from wheels**

```bash
rm -rf dist && uv build --package prumi --out-dir dist && uv build --package prumi-adapter-dbt --out-dir dist
docker build -f deploy/image/Dockerfile --build-arg PRUMI_WHEELS=dist -t prumi:dev .
docker build -f deploy/demo-image/Dockerfile --build-arg PRUMI_IMAGE=prumi:dev -t prumi-demo:dev .
```
Expected: both builds succeed.

- [ ] **Step 2: Check the main image without a config**

Run: `docker run --rm prumi:dev; echo "exit=$?"`
Expected: `prumi: The config file /prumi/prumi.toml doesn't exist.` and a non-zero exit.

- [ ] **Step 3: Check the demo image by hand**

Run: `docker run --rm -d --name prumi-demo-dev -p 127.0.0.1:8484:8484 prumi-demo:dev && sleep 20 && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8484/ && docker logs prumi-demo-dev | head -3 && docker rm -f prumi-demo-dev`
Expected: `200`, and the welcome link is the first log line.

- [ ] **Step 4: Check the final images carry no dbt and the main image no demo**

Run: `docker run --rm --entrypoint sh prumi:dev -c 'command -v dbt; command -v uv; ls /demo' ; docker image ls prumi:dev prumi-demo:dev`
Expected: no `dbt`, no `uv`, no `/demo`; record both sizes for the PR description.

- [ ] **Step 5: Commit**

```bash
git add deploy/image deploy/demo-image .dockerignore
git commit -m "Add the main and demo container images"
```

### Task 5: The image smoke test

**Files:**
- Create: `scripts/check_images.py`

**Interfaces:**
- Consumes: Task 4's images, by reference.
- Produces: `uv run python scripts/check_images.py --image <ref> --demo-image <ref>`; exit 0 on success, a message naming the failed check otherwise. Used by Task 6's workflows.

The script follows `scripts/check_install.py`'s style (`_step`, `_run`, a start timeout of 120 s, polling `GET /`). Checks, in order:

1. `docker run --rm <image>` exits non-zero and its stderr contains `/prumi/prumi.toml`.
2. `docker run -d -p 127.0.0.1:<free port>:8484 -v <fresh named volume>:/data <demo-image>`: the first log line contains `#try-prumi-on-it`; `GET /` returns 200.
3. Over MCP (`mcp` client, as `packages/prumi-core/tests/test_docs.py` uses it) at `http://127.0.0.1:<port>/mcp`: `publish_dashboard` with a small demo spec (one KPI block on `revenue`) returns status `draft` and a `url` starting `http://127.0.0.1:<port>/d/`; `GET <url>` returns 200.
4. A request with `Host: evil.example:8484` returns 403.
5. `docker exec <container> prumi certify <id> 1` fails mentioning `--by`; with `--by "Smoke Test"` it succeeds.
6. A second demo container with `-p 127.0.0.1:9000:8484` (or another free port): the published link names that port.
7. Every container and volume it created is removed, also on failure.

- [ ] **Step 1: Run it against the local images**

Run: `uv run python scripts/check_images.py --image prumi:dev --demo-image prumi-demo:dev`
Expected: every step printed, then `check_images: passed in N s`.

- [ ] **Step 2: Break it on purpose once** — run with `--demo-image prumi:dev` (the main image)

Expected: fails at check 2 with a message naming the welcome line or the home page.

- [ ] **Step 3: Commit**

```bash
git add scripts/check_images.py
git commit -m "Add a smoke test for the container images"
```

### Task 6: CI and the release workflow

**Files:**
- Modify: `.github/workflows/ci.yml` (new job `images`)
- Modify: `.github/workflows/release.yml` (new job `images`; `publish-release` needs it)

**Interfaces:**
- Consumes: Task 4's Dockerfiles and build args, Task 5's script.

- [ ] **Step 1: CI job `images`** — on every PR and push to `main`: checkout, setup-uv, `uv build` both packages into `dist/`, `docker/setup-buildx-action`, build both images for `linux/amd64` with `--load` (the main one with `PRUMI_WHEELS=dist`), run `scripts/check_images.py` on them. Nothing is pushed.

- [ ] **Step 2: Release job `images`** — `needs: [upload-prumi, upload-prumi-adapter-dbt]`, `if: github.event_name == 'push'`, `permissions: contents: read, packages: write`. Checks out the tag; waits until `pip download prumi[dbt]==<version> --no-deps` (or `uv pip compile`) sees the version on PyPI (retry up to 10 minutes); logs in to GHCR with `GITHUB_TOKEN`; sets up QEMU and Buildx; builds the main image for `linux/amd64` with `--load`, builds the demo image on it, runs `scripts/check_images.py`; then builds and pushes both for `linux/amd64,linux/arm64` with tags `ghcr.io/prumihq/prumi:<version>`, `:latest`, and the same for `prumi-demo` (the demo's `PRUMI_IMAGE` is `ghcr.io/prumihq/prumi:<version>`). The version is the tag without its `v`. `publish-release` gains `images` in `needs`.

- [ ] **Step 3: Check the workflows parse**

Run: `uvx actionlint .github/workflows/ci.yml .github/workflows/release.yml` (or `uvx zizmor` if actionlint isn't available as a uv tool; otherwise `python -c "import yaml,sys; [yaml.safe_load(open(f)) for f in sys.argv[1:]]" ...`)
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/release.yml
git commit -m "Build and smoke-test the images in CI, and publish them on release"
```

### Task 7: Docs and the ADR

**Files:**
- Create: `docs/adr/0017-docker-is-the-gate-in-the-container.md`
- Modify: `README.md`, `examples/demo-dbt-project/README.md`, `docs/configuration.md`, `CONTRIBUTING.md`, `docs/releasing.md`
- Test: `packages/prumi-core/tests/test_docs.py` (only if a list it checks changes; the CLI's `--by` help is not a list)

- [ ] **Step 1: ADR 0017**, status `Proposed · 2026-11-03`, in the style of `docs/adr/0013-registry-sqlite.md`: Context (no login, loopback-only rule, a container's loopback is its own), Decision (`PRUMI_CONTAINER=1` set by the image; `0.0.0.0` default inside; loopback `Host` names on any port; links from the request's `Host`; `-p 127.0.0.1:` everywhere; `certify --by` required), Considered (dropping the loopback rule; a `serve` flag), Consequences (Docker's publishing is the gate, `-p 8484:8484` would expose it and Prumi can't see that; ends with login). Add it to `docs/adr/README.md`'s list if it has one.

- [ ] **Step 2: `docs/configuration.md`**: the `host` row (`127.0.0.1`, or `0.0.0.0` in the container image) and a new `## Running in a container` section: the two-column table from the spec, `PRUMI_CONTAINER`, why `127.0.0.1:` stays in `-p`, `certify --by`, one line that Chromium's own sandbox is off (link #31).

- [ ] **Step 3: `README.md`**: a `## Try it` section right after the intro/status note, before "Why Prumi": the demo `docker run` line, `claude mcp add --scope user --transport http prumi http://127.0.0.1:8484/mcp`, a link to the demo page, and one sentence: keep `127.0.0.1:` in `-p`, Prumi has no login yet (link to the configuration section). The tip under "Use it with your metrics" points to "Try it". Troubleshooting's "`host` isn't a loopback address" adds the container case. Roadmap: the trial leaves "Team use"'s list (compose stays until its own PR).

- [ ] **Step 4: `examples/demo-dbt-project/README.md`**: "Try Prumi on it" leads with the `docker run` line, the optional `-v prumi-demo:/data`, and `docker exec <container> prumi list` / `prumi certify <id> 1 --by "Your Name"`; then "Without a container engine, or to work on Prumi:" and today's clone and `uv` steps unchanged.

- [ ] **Step 5: `CONTRIBUTING.md`**: `deploy/` in the repository layout table; a short "The container images" section with Task 4 Step 1's commands and `scripts/check_images.py`. `docs/releasing.md`: the images job, and the maintainer's one-time steps (after the first release, make the `prumi` and `prumi-demo` GHCR packages public and link them to the repository).

- [ ] **Step 6: Run the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check && uv run ruff format --check`
Expected: PASS, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add docs/adr/0017-docker-is-the-gate-in-the-container.md docs/adr/README.md README.md examples/demo-dbt-project/README.md docs/configuration.md CONTRIBUTING.md docs/releasing.md docs/superpowers/specs/2026-11-03-container-image-design.md docs/superpowers/plans/2026-11-03-container-image.md
git commit -m "Document the container images and propose ADR 0017"
```

### Task 8: PR and merge

- [ ] **Step 1:** `git push -u origin feat/container-image`; open the PR with `gh pr create`, title from Global Constraints, body: summary, `Closes #29`, image sizes, the docs changed, the attribution line.
- [ ] **Step 2:** Wait for CI (`gh pr checks --watch`). Fix failures on the branch.
- [ ] **Step 3:** Squash-merge once green (`gh pr merge --squash --delete-branch`), then `git switch main && git pull`.
