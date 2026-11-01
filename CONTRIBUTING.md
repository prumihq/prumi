# Contributing to Prumi

[README.md](README.md) says what Prumi is and how to use it. This file is for working on Prumi itself.

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

[AGENTS.md](AGENTS.md) describes how work is organized (branches, designs and plans, ADRs, code style, which docs a change must update), for people and coding agents alike.

With Claude Code, the repo enables the [Superpowers](https://github.com/obra/superpowers) plugin in `.claude/settings.json`. Install it once with `claude plugin install superpowers@claude-plugins-official --scope project`.

## The install check

The install check tries Prumi as users get it from PyPI, without uploading anything: it builds both packages, installs them with `uv tool install "prumi[dbt]"` into a temporary tool directory (never your own), runs `prumi install-chromium`, starts `prumi serve` on the demo and opens its home page, and runs `twine check` on the built files. It isn't part of `uv run pytest`; CI runs it as a job of its own.

```sh
uv run python scripts/check_install.py
```

## The README's diagrams

The two diagrams in the README are drawn by a script, each in a light and a dark version. Edit the script, not the SVGs, and run it again; `--preview DIR` also renders PNGs to look at.

```sh
uv run python scripts/make_readme_images.py --preview /tmp/readme-images
```

## Releases

Releases go to PyPI from GitHub Actions on a version tag; [docs/releasing.md](docs/releasing.md) is the maintainer's checklist.

## Repository layout

| Path | What it is |
|---|---|
| `packages/prumi-core` | The `prumi` package: the server and the `prumi` command (MCP endpoint, catalog, query engine, validator, registry) |
| `packages/prumi-adapter-dbt` | The `prumi-adapter-dbt` package: dbt adapter (MetricFlow compiles, Prumi executes) |
| `packages/prumi-runtime` | Browser runtime that dashboards load; the `prumi` package carries a copy |
| `examples/demo-dbt-project` | Demo dbt project on DuckDB, used for tests and the trial |
| `docs/superpowers/` | Designs and implementation plans |
| `docs/` | The BI rules and issue codes, configuration, releasing, and architecture decision records (`docs/adr/`) |
| `scripts/` | The install check, the release's tag check and the README's diagrams |
| `spikes/` | Throwaway experiments |

## Security

Please report vulnerabilities privately, as [SECURITY.md](SECURITY.md) describes, not in a public issue.
