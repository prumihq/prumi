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

- **Specs drive the work.** Each planned minor release has a spec folder named by goal, `specs/<goal>/` with `spec.md` (what and why) and `tasks.md` (ordered steps); see `specs/README.md` for the terms and when a spec may change. Implement from the current spec, and only from its accepted sections. Don't build anything the spec doesn't ask for, and never edit a spec whose status is `done`.
- **Don't change scope or fill gaps by guessing.** If a spec is unclear, contradicts itself or turns out to be wrong, write the question in `specs/<goal>/questions.md` (what you found, the options you see, which task is blocked) and stop. The maintainer answers and updates the spec.
- **Milestones are not version numbers.** Releases stay 0.x; never tag 1.0 unless the maintainer says so.
- **ADRs record technical decisions** in `docs/adr/`. Add one when a decision would surprise a future reader. Agents propose ADRs with status Proposed; only the maintainer accepts them. Never rewrite an accepted ADR; supersede it with a new one.
- **Spikes are throwaway.** Code in `spikes/` answers one question and is never imported by `packages/`.
- This repo holds code and technical docs only.

## Rules that shape the code

The BI rules R1–R10 and their issue codes are explained in `docs/bi-rules.md`.

- **Numbers only come from the source.** The engine never re-implements a metric definition and never re-aggregates non-additive metrics (ratios, averages, distinct counts). Dashboards get every number through the runtime.
- **Access rules belong to the source.** Prumi passes the viewer's identity; it never decides who sees which rows.
- **Adapters are separate packages** behind the adapter protocol in `prumi-core`. Core never imports an adapter directly.
- **Database dialects are not Prumi's job.** For dbt, MetricFlow generates the SQL.

## Docs are part of the task

A task isn't done until the docs it affects match the code, in the same commit:

- `CHANGELOG.md`: a line for each change a user can see: an MCP tool, a CLI command, a config key, a page, a rule or issue code, a limitation.
- `README.md`: when the quick start, a command, an MCP tool, a config key or where data goes changes.
- A package's `README.md`: when its public API changes (`window.prumi`, the adapter protocol, `config.meta.prumi`).
- `docs/bi-rules.md`: when a BI rule or an issue code changes.
- `docs/adr/`: never edit an accepted ADR. If your change makes one wrong, write it in `questions.md` and stop.

Your report lists the docs you changed, or says that none were affected.

## Layout

- `packages/prumi-core` (import `prumi`): server, MCP endpoint, catalog, engine, validator, registry, adapter protocol
- `packages/prumi-adapter-dbt` (import `prumi_adapter_dbt`): dbt adapter
- `packages/prumi-runtime`: browser runtime, plain JS, no build step
- `examples/demo-dbt-project`: a stand-in "customer" project for tests and the trial; not product code

## Code style

- Python 3.12+, type hints on public functions, ruff for lint and format.
- Tests live next to each package in `tests/`.
- **Code never cites specs.** Specs are plans for one release and freeze when it ships; the code outlives them. Comments explain the *why* in their own words, without "spec section N" or "AC N". When a pointer helps, point to something lasting: an ADR (`docs/adr/0002`), a BI rule (`R6`) or an issue code, all documented in `docs/`.
- Tests are named after the behavior they check. Which task serves which acceptance criterion is recorded in `tasks.md`, not in the code.
- **Tests never read files under `specs/`.** Text the product ships (such as the MCP instructions) lives in the code or in a file shipped with the package, and tests compare against that.

## Commits

- Short imperative subject line; explain why in the body when it isn't obvious.
- AI-assisted commits end with a `Co-Authored-By:` trailer naming the model.
- Commit when a task is done. Don't push unless the maintainer asks.
