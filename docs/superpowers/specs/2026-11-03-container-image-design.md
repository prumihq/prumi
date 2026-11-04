# The container image, and a demo image to try Prumi

Status: draft, for the maintainer's review. Issue: #29 (milestone 0.3). First of three 0.3 designs; the others are `2026-11-03-settings-and-postgres-registry-design.md` (#30, #28) and `2026-11-03-compose-install-design.md` (#12), built in that order.

## Why

0.3 makes the container the main way to run Prumi, before anyone outside the project uses it. Chromium is the hardest part of installing Prumi: its system packages depend on the Linux version, and CI relies on `playwright install --with-deps`. An image fixes the Python, MetricFlow, Chromium and driver versions, so there is one environment to support and one setup guide. Trying Prumi today also means cloning the repo, installing uv and Chromium and building the demo: a lot of steps before someone sees a dashboard.

Success: someone with Docker runs one line, connects their agent with a second, and is building a dashboard on the demo shop, with nothing cloned or installed.

## Goals

1. **A main image, `ghcr.io/prumihq/prumi`,** that runs `prumi` with the dbt adapter, the DuckDB and Postgres drivers and Chromium, for a team's own project.
2. **A demo image, `ghcr.io/prumihq/prumi-demo`,** that serves the demo shop with no other argument:
   ```sh
   docker run -p 127.0.0.1:8484:8484 ghcr.io/prumihq/prumi-demo
   ```
3. **Prumi stays off the network** in both, while it has no login.
4. **Both images are published** to GHCR on every release.
5. **The docs lead with the container** for trying Prumi.

## Non-goals

- The `docker compose` install, settings from environment variables, and the Postgres registry. They are the next two designs. This one adds nothing they would have to undo.
- A `prumi demo` command, or demo data in the PyPI packages.
- dbt in either final image. Prumi reads only `target/semantic_manifest.json` and never runs dbt.
- One image per warehouse. Revisit when heavy drivers (Snowflake, BigQuery, Databricks) arrive.
- Image signing, SBOMs and provenance attestations. *My call:* not now; nothing here should prevent adding them.
- Testing on Docker Desktop (Mac, Windows) or Podman. The images should work there, but only Docker Engine on Linux is tested.
- Login. Until it exists, Docker's port publishing is the gate (below).
- **Rebuilding the images between releases** for security fixes. The maintainer moved it out of 0.3 to plan later (#32): it needs a decision on what a version tag promises after a rebuild. The images are built on releases only.
- **Turning on Chromium's sandbox** (#31, milestone 0.4). See "Chromium".

## Anti-goals

The work fails, even if the images run, if:

- a documented command or example makes Prumi reachable from another machine;
- a `uv` install can listen on a non-loopback address without someone deliberately setting the container variable;
- the main image, or a PyPI package, carries demo data;
- the main image ever serves the demo: a mistyped mount path must fail, not silently show the demo shop;
- trying Prumi takes more than the `docker run` line and the agent's `claude mcp add` line.

## Constraints

- AGENTS.md's rules: core never imports an adapter, no version numbers changed by hand, no tags created, releases stay 0.x.
- The main image is built from the **released PyPI packages**, so what users get from PyPI and from the image is the same code. CI keeps testing the `uv tool install "prumi[dbt]"` path (`scripts/check_install.py`), which stays documented as the alternative.
- The demo image needs no network to start.
- The port stays 8484.

## Decided by the maintainer

- **The container is the primary install; `uv` is the documented alternative.** "Primary" is about what the docs lead with. The code doesn't refuse to run outside the image.
- **Two images, so production carries no demo weight.** The maintainer's rule: nothing useless in the image or the PyPI packages for someone installing Prumi for production. The demo image is built `FROM` the main image of the same version, plus the demo data. `uv` users try the demo with the existing clone-and-build steps.
- **The demo stays on DuckDB,** its data inside the image, so the trial is a single container.
- **The demo's registry is SQLite inside the container.** Dashboards disappear with the container, which is fine for a trial. A volume keeps them for anyone who wants that (`-v prumi-demo:/data`), shown on the demo page as an optional step; the README line stays without it.
- **A welcome link:** on start, the demo image prints one link, to the demo page on GitHub (`examples/demo-dbt-project/README.md#try-prumi-on-it`).
- **The main image never falls back to the demo.** Started without a configuration, it stops with an error naming the path it looked for.
- **The loopback rule stays.** Outside a container, `host` must still be a loopback address. Inside the image, Prumi listens on every address of the container, and Docker's `-p 127.0.0.1:8484:8484` is what keeps it reachable from the host machine only. Every `docker run` and compose example includes the `127.0.0.1:` part, and the docs say why. Login (the next release) makes Prumi its own gate again.
- **Where that is documented:** a Proposed ADR (the reasoning), `docs/configuration.md` (the behavior), the README (the warning, next to the commands), and one-line comments in the code pointing to the ADR. See "Docs".
- **One image for every warehouse Prumi supports** (DuckDB and Postgres drivers today).
- **Published to GHCR,** built from the released PyPI packages on every release. A scheduled rebuild between releases was agreed in principle, then moved out of 0.3 by the maintainer, to plan later (#32).
- **Chromium's sandbox stays as it is today (off) in 0.3,** in the image and outside it. See "Chromium".
- **The README gets a "Try it" section** near the top: the `docker run` line, the `claude mcp add` line and a link to the demo page, and no `uv`. The demo page leads with `docker run`; the current clone and `uv` steps follow, for contributors and anyone without a container engine.
- **The maintainer lets the agent squash-merge this PR** once CI is green.

## Listening inside the container

The image sets one environment variable. *My call:* `PRUMI_CONTAINER=1`. A `uv` install doesn't have it unless someone sets it on purpose.

| | `PRUMI_CONTAINER` unset (a `uv` install) | `PRUMI_CONTAINER=1` (the image) |
|---|---|---|
| Default `host` | `127.0.0.1`, as today | `0.0.0.0` |
| A non-loopback `host` in the config | refused, today's message | allowed |
| Host names the server answers | as today: `<host>:<port>` and `localhost:<port>` | `localhost`, `127.0.0.1` and `[::1]`, on any port |

- **The host-name check** (`HostCheck`, `allowed_hosts` in `server/guards.py`) blocks DNS rebinding, and stays on in both modes. Inside the container today's rule would break: listening on `0.0.0.0` it would answer only `0.0.0.0:8484` and `localhost:8484`, and refuse a browser at `127.0.0.1:8484`. *My call:* in the image it answers the loopback names on any port, so `-p 127.0.0.1:9000:8484` works too. Rebinding is blocked by the name, not the port.
- **The links Prumi hands out.** Prumi builds URLs from `host` and `port` today: the dashboard links in MCP results (`_dashboard_url` in `mcp/__init__.py`), the MCP address on the home page (`server/app.py`), and the "this link only works on this machine" note, shown when `host` is loopback. With `host = 0.0.0.0` the agent would get `http://0.0.0.0:8484/d/…`, which the host-name check itself refuses. *My call:* in the image, those URLs are built from the request's own `Host` header, which the host-name check has already limited to loopback names. So a remapped port (`127.0.0.1:9000`) gives right links too. The note shows in the image as well. Outside a container nothing changes.
- **Only `1` turns it on.** *My call:* any other value is treated as unset, so `PRUMI_CONTAINER=0` doesn't open anything.
- *My call:* in the image, a config that sets a loopback `host` (a copied demo config, say) starts, but logs a warning that the server can't be reached from outside the container.
- The refusal message outside the container stays as it is. It doesn't mention `PRUMI_CONTAINER`, so nobody is nudged to set it on a laptop.
- Tests cover both columns, the value rule, and the host-name check in both modes, including a refused `Host: evil.example:8484`.

## The main image

- **Contents:** Python, `prumi[dbt]` at the release's version from PyPI (which brings the DuckDB and Postgres drivers), Chromium and the system libraries it needs. No dbt, no demo data, no uv.
- **Architectures:** `linux/amd64` and `linux/arm64` (Apple Silicon laptops run arm64). *My call.*
- **User:** a non-root user. *My call.*
- **Layout:** *my call:* the working directory is a fixed folder (say `/prumi`), the entrypoint is `prumi` and the default command is `serve`. So `docker run … ghcr.io/prumihq/prumi` with nothing mounted stops with today's message, `The config file /prumi/prumi.toml doesn't exist.`, naming the path. A user mounts their `prumi.toml` there, and the paths in it are paths inside the container. The next design makes the file optional.
- **Other commands** run the same way: `docker exec <container> prumi list`, `… prumi certify <id> 1 --by "Name"`.
- **`certify` without `--by`:** it defaults to the OS user name, which in the image is the image's user, so a certification would read "certified by prumi". *My call:* when `PRUMI_CONTAINER=1`, `certify` without `--by` refuses and asks for a name.
- **A healthcheck,** *my call*, built so it doesn't need anything the image doesn't already have; the home page answers loopback names, so a request to `127.0.0.1:8484` from inside works.
- **Tags:** the version (`0.3.0`) and `latest`. The builder may add `0.3`.
- **Size:** Chromium makes it large. Keep it as small as is reasonable (slim base, no build tools left in the final layer), and record the size in the PR.

## Chromium

Chromium's own sandbox is **off today**, everywhere: Prumi starts Chromium with `playwright.chromium.launch()` (`validate/page.py`), and Playwright passes `--no-sandbox` unless told `chromium_sandbox=True`. The dashboard frame's iframe sandbox (ADR 0004) is a different thing and stays on.

- **Decided by the maintainer:** 0.3 keeps today's behavior, in the image and outside it. Turning the sandbox on everywhere is #31, planned with the next release's security work (login, HTTPS). In a container it usually needs extra `docker run` flags, which would break the one-line trial.
- So the image needs no sandbox setup, and `prumi`'s launch of Chromium doesn't change.
- The image still runs as a non-root user, which limits what a page that breaks out of Chromium can reach inside the container.
- `docs/configuration.md`'s container section says, in one line, that Chromium's sandbox is off and links #31, so nobody assumes otherwise.

## The demo image

- **Built `FROM` the main image of the same version.** A throwaway first stage runs dbt and `examples/demo-dbt-project/build.py` (DuckDB) on the repo at the release tag. Only `demo.duckdb` and `target/semantic_manifest.json` are copied into the final image.
- **Its own config file,** written for the image, not the repo's `examples/demo-dbt-project/prumi.toml`: that one sets `host = "127.0.0.1"`, which can't be reached from outside a container. It keeps the demo's `as_of`, locale and allowed hosts, leaves `host` to the image's default, and puts `data_dir` at `/data`. *My call:* the file lives next to the demo image's Dockerfile.
- **Starts the demo by default** (`docker run … ghcr.io/prumihq/prumi-demo`, no argument), and prints the welcome link once on start, before the server's own output. *My call:* no new `prumi` command; the image's default command prints the link and starts `prumi serve` with its config.
- **`/data`** holds the SQLite registry; the optional volume goes there. It must be writable by the image's user.
- **Same tags and architectures** as the main image.

## Building and publishing

- **Dockerfiles in a new `deploy/` folder.** *My call;* the compose file joins them in the third design.
- **On every pull request** (*my call:* `amd64` only, for speed): build the main image from the branch's wheels instead of PyPI, build the demo image on it, and run a smoke test:
  - the main image with no config exits non-zero with the message naming `/prumi/prumi.toml`;
  - the demo image, published on `127.0.0.1`, answers the home page, and a demo dashboard publishes and validates (so Chromium works in the image), and the link the publish returns opens;
  - the same with the port remapped (`127.0.0.1:9000:8484`): the returned link names port 9000 and opens;
  - a request with a foreign `Host` header is refused.
- **On a release tag:** after the packages are on PyPI, build both images for both architectures from the released version, run the same smoke test, and push them to GHCR with their tags. The release workflow's order is kept: the GitHub Release is published once everything it announces exists. *My call:* the images are pushed before the release is published, so the release never points to an image that isn't there.
- **No scheduled rebuild** in 0.3 (#32). *My call:* the release job builds from the release tag's Dockerfiles and demo project, so a later rebuild can reuse it.
- **No secrets stored:** GitHub's own token pushes to GHCR (`packages: write` on that job only).
- **One-time steps for the maintainer** (making the GHCR packages public, and anything else GitHub needs) go in `docs/releasing.md`.
- Workflow actions are pinned by commit, as the existing workflows are.

## Docs

- **`README.md`:** a "Try it" section near the top (the `docker run` line with `127.0.0.1:`, the `claude mcp add` line, a link to the demo page, and one sentence on why `127.0.0.1:` stays). The demo tip under "Use it with your metrics" points to it. "Use it with your metrics" itself stays the `uv` install until the compose design rewrites it. The roadmap and known limitations lose what this ships; troubleshooting's "`host` isn't a loopback address" mentions the container case.
- **`examples/demo-dbt-project/README.md`:** "Try Prumi on it" leads with `docker run` (and the optional volume, and `docker exec … prumi certify … --by`), followed by the clone and `uv` steps.
- **`docs/configuration.md`:** the `host` row updated, and a "Running in a container" section: the table above, what `PRUMI_CONTAINER` does, why `127.0.0.1:` must stay in `-p`, and `certify --by`.
- **A Proposed ADR (0017):** "Inside the container, Docker's port publishing is the gate": the problem, the variable, the host-name change, that it ends with login.
- **`CONTRIBUTING.md`:** the `deploy/` folder in the repository layout, and how to build and run the images locally.
- **`docs/releasing.md`:** the images in the release, and the maintainer's one-time GHCR steps.

## Left to the builder

- The base image and how Chromium and its libraries are installed in it.
- How the release workflow waits for PyPI to serve the new version before building from it.
- The healthcheck's command and timings.
- How the smoke test drives a dashboard (an MCP call, or the CLI's `validate` inside the container).
- Whether the welcome link comes from a small entrypoint script or another way.
