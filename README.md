# Prumi

> **Bring shadow BI into the light.**

Prumi is a self-hosted server with a remote MCP endpoint. It lets people build dashboards with their own AI agent on top of their company's official metrics, with correct BI behavior and a path to certification by the data team.

- **Free presentation, governed numbers.** The agent can write any HTML; the numbers only come from Prumi.
- **Metrics live in the source.** Prumi reads your dbt semantic models; it never re-implements a metric.
- **Access rules belong to the source.** Queries run as the viewer, and the warehouse enforces access.

## Status

Early development. Nothing is usable yet.

## Repository layout

| Path | What it is |
|---|---|
| `packages/prumi-core` | The server: MCP endpoint, catalog, query engine, validator, registry |
| `packages/prumi-adapter-dbt` | dbt adapter (MetricFlow compiles, Prumi executes) |
| `packages/prumi-runtime` | Browser runtime that dashboards load |
| `examples/demo-dbt-project` | Demo dbt project on DuckDB, used for tests and the trial |
| `specs/` | Specs and task lists per milestone |
| `docs/adr/` | Architecture decision records |
| `spikes/` | Throwaway experiments |

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest
uv run ruff check
```

## License

[Apache 2.0](LICENSE)
