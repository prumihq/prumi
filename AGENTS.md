# AGENTS.md

Instructions for coding agents working in this repo. Read `README.md` for what Prumi is.

## Commands

```sh
uv sync                 # install the workspace
uv run pytest           # all tests
uv run ruff check       # lint
uv run ruff format      # format
```

## How work is organized

- **Specs drive the work.** Each milestone has `specs/<milestone>/spec.md` (what and why) and `tasks.md` (ordered steps). Implement from the current milestone's spec. Don't build anything the spec doesn't ask for.
- **Don't change scope or fill gaps by guessing.** If a spec is unclear, contradicts itself or turns out to be wrong, write the question in `specs/<milestone>/questions.md` (what you found, the options you see, which task is blocked) and stop. The maintainer answers and updates the spec.
- **Milestones are not version numbers.** Releases stay 0.x; never tag 1.0 unless the maintainer says so.
- **ADRs record technical decisions** in `docs/adr/`. Add one when a decision would surprise a future reader. Never rewrite an accepted ADR; supersede it with a new one.
- **Spikes are throwaway.** Code in `spikes/` answers one question and is never imported by `packages/`.
- This repo holds code and technical docs only.

## Rules that shape the code

- **Numbers only come from the source.** The engine never re-implements a metric definition and never re-aggregates non-additive metrics (ratios, averages, distinct counts). Dashboards get every number through the runtime.
- **Access rules belong to the source.** Prumi passes the viewer's identity; it never decides who sees which rows.
- **Adapters are separate packages** behind the adapter protocol in `prumi-core`. Core never imports an adapter directly.
- **Database dialects are not Prumi's job.** For dbt, MetricFlow generates the SQL.

## Layout

- `packages/prumi-core` (import `prumi`): server, MCP endpoint, catalog, engine, validator, registry, adapter protocol
- `packages/prumi-adapter-dbt` (import `prumi_adapter_dbt`): dbt adapter
- `packages/prumi-runtime`: browser runtime, plain JS, no build step
- `examples/demo-dbt-project`: a stand-in "customer" project for tests and the trial; not product code

## Code style

- Python 3.12+, type hints on public functions, ruff for lint and format.
- Tests live next to each package in `tests/`.

## Commits

- Short imperative subject line; explain why in the body when it isn't obvious.
- AI-assisted commits end with a `Co-Authored-By:` trailer naming the model.
