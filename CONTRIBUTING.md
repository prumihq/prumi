# Contributing to Prumi

[README.md](README.md) says what Prumi is and how to use it. This file is for working on Prumi itself.

## Start with an issue

For anything bigger than a small fix, [open an issue](https://github.com/prumihq/prumi/issues/new/choose) or comment on an existing one before you start, and say you're working on it. Then others know it's taken, and we can agree on the approach early, before you spend time on code. Small fixes (a typo, a broken link, a one-line bug fix) can go straight to a pull request. Link the issue in your pull request with `Closes #N`; merging then closes it.

## Using AI tools

Prumi is itself developed with AI assistance, and AI-assisted contributions are welcome on three conditions: say in the pull request which tools you used, understand every line you submit, and answer review comments yourself. You're responsible for the change, whoever typed it.

## Set up and run the tests

You need [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/prumihq/prumi && cd prumi
uv sync
uv run prumi install-chromium --with-deps
uv run pytest        # builds the demo project on first use
uv run ruff check
uv run ruff format
```

Like the server, the tests need headless Chromium. The full suite takes about 4 minutes, because most tests render a page in Chromium.

### The Postgres tests

Tests that need a Postgres server skip unless `PRUMI_TEST_POSTGRES_HOST` is set. Start one and point the tests at it:

```sh
docker run -d --name prumi-postgres -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:5432:5432 postgres:18
PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest
```

The tests build the demo into a database `prumi_demo` on that server. The registry's tests run on it too: each one creates a throwaway `prumi_registry_…` database, and a few a `prumi_registry_…` role, and drops them when it ends, so the test user must be allowed to create databases and roles (the image's `postgres` superuser is). Also set `PRUMI_TEST_POSTGRES_PORT`, `PRUMI_TEST_POSTGRES_USER` (default `postgres`) and `PRUMI_TEST_POSTGRES_PASSWORD` (default `postgres`) if yours differ. Set `PRUMI_REQUIRE_POSTGRES=1` to make a missing server a failure instead of a skip.

[AGENTS.md](AGENTS.md) describes how work is organized (branches, designs and plans, ADRs, code style, which docs a change must update), for people and coding agents alike.

Prumi is developed with the [Superpowers](https://github.com/obra/superpowers) skills, and they're recommended for AI-assisted work here. They're available for Claude Code, Codex, Cursor, Gemini and others (see their README). With Claude Code, the repo enables the plugin in `.claude/settings.json`; install it once with `claude plugin install superpowers@claude-plugins-official --scope project`. What every change needs, however it was made, is a passing CI and the maintainer's review.

## The install check

The install check tries Prumi as users get it from PyPI, without uploading anything: it builds both packages, installs them with `uv tool install "prumi[dbt]"` into a temporary tool directory (never your own), runs `prumi install-chromium`, starts `prumi serve` on the demo and opens its home page, and runs `twine check` on the built files. It isn't part of `uv run pytest`; CI runs it as a job of its own.

```sh
uv run python scripts/check_install.py
```

## The container images

`deploy/` holds the Dockerfiles: `deploy/image` for the main image and `deploy/demo-image` for the demo shop on top of it. Both build from the repository root. To build them from your working tree, build the wheels first: the main image installs them instead of the packages on PyPI.

```sh
rm -rf dist && uv build --package prumi --out-dir dist && uv build --package prumi-adapter-dbt --out-dir dist
docker build -f deploy/image/Dockerfile --build-arg PRUMI_WHEELS=dist -t prumi:dev .
docker build -f deploy/demo-image/Dockerfile --build-arg PRUMI_IMAGE=prumi:dev -t prumi-demo:dev .
docker run --rm -p 127.0.0.1:8484:8484 prumi-demo:dev
```

The smoke test runs both images the way the README says and checks what they promise: the main image without a config stops and names `/prumi/prumi.toml`, the demo publishes and validates a dashboard over MCP with a link to the port it was reached on, also when the port is remapped, a foreign `Host` header is refused, and `certify` needs `--by`. Like the install check, it isn't part of `uv run pytest`; CI runs it as a job of its own, and the release runs it before pushing the images.

```sh
uv run python scripts/check_images.py --image prumi:dev --demo-image prumi-demo:dev
```

## The compose install

`deploy/compose/` holds the `docker-compose.yml` and `.env.example` of the [compose install](docs/install.md). Each release attaches both to its GitHub Release (the example as `env.example`: GitHub renames an asset whose name starts with a period), which is where users download them from, and release-please moves the image's version in `docker-compose.yml`: don't edit it by hand. Between releases, `main`'s copy can name an image that isn't published yet.

`scripts/check_compose.py` is the compose install's smoke test: it runs the shipped `docker-compose.yml`, unchanged but for the image, against a locally built main image and a Postgres warehouse with the demo built on it, and checks that Prumi publishes and certifies a dashboard, keeps its registry in the bundled database, and picks up a new manifest through the mounted project folder. Like the other checks, it isn't part of `uv run pytest`; CI runs it as the job `compose`. `--help` lists its options.

```sh
uv run python scripts/check_compose.py --help
```

## The README's diagrams

The two diagrams in the README are drawn by a script, each in a light and a dark version. Edit the script, not the SVGs, and run it again; `--preview DIR` also renders PNGs to look at.

```sh
uv run python scripts/make_readme_images.py --preview /tmp/readme-images
```

## How releases work

- Pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat: add the X tool`), and a check enforces it. The pull request template lists the types.
- A `feat` title gives a minor release, a `fix` title a patch.
- The title becomes the pull request's line in the changelog, so write it for the people who use Prumi.
- A maintainer releases by merging the release PR; [docs/releasing.md](docs/releasing.md) is the maintainer's manual.

## Repository layout

| Path | What it is |
|---|---|
| `packages/prumi-core` | The `prumi` package: the server and the `prumi` command (MCP endpoint, catalog, query engine, validator, registry) |
| `packages/prumi-adapter-dbt` | The `prumi-adapter-dbt` package: dbt adapter (MetricFlow compiles, Prumi executes) |
| `packages/prumi-runtime` | Browser runtime that dashboards load; the `prumi` package carries a copy |
| `examples/demo-dbt-project` | Demo dbt project on DuckDB, used for tests and the trial |
| `deploy/` | The Dockerfiles of the main image (`deploy/image`) and the demo image (`deploy/demo-image`, with its config and start script), and the compose install's files (`deploy/compose`) |
| `docs/superpowers/` | Designs and implementation plans |
| `docs/` | The BI rules and issue codes, the compose install, configuration, releasing, and architecture decision records (`docs/adr/`) |
| `scripts/` | The install check, the images' and the compose install's smoke tests, the release's tag check and the README's diagrams |
| `release-please-config.json`, `.release-please-manifest.json` | What the release PR bumps, and the current version |
| `.github/` | The workflows (CI, the PR title check, the release PR and the release), the issue and pull request templates, and the Dependabot config for the actions |
| `spikes/` | Throwaway experiments |

## Security

Please report vulnerabilities privately, as [SECURITY.md](SECURITY.md) describes, not in a public issue.
