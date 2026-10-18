# Prumi

> **Bring shadow BI into the light.**

Prumi is a self-hosted server with a remote MCP endpoint. It lets people build dashboards with their own AI agent on top of their company's official metrics, with correct BI behavior and a path to certification by the data team.

- **Free presentation, governed numbers.** The agent can write any HTML; the numbers only come from Prumi.
- **Metrics live in the source.** Prumi reads your dbt semantic models; it never re-implements a metric.
- **Access rules belong to the source.** Queries run as the viewer, and the warehouse enforces access.

## Status

Version 0.1 proves the core loop on one machine, for one local user, against a demo dbt project on DuckDB: an agent finds the official metrics, explores them, writes a dashboard and publishes it; the data team certifies it; any later edit makes it a draft again. It isn't ready to install for a company yet: there's no login, and the server only listens on your own machine.

## Quick start

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync

# Build the demo warehouse and semantic manifest (about 10 seconds)
uv run --project examples/demo-dbt-project python examples/demo-dbt-project/build.py

# Start Prumi on http://127.0.0.1:8484
uv run prumi serve --config examples/demo-dbt-project/prumi.toml
```

Then [connect your agent](#connect-your-agent) and ask it for a dashboard, e.g. "Build a dashboard of weekly revenue by region, with average order value." The agent finds the metrics, checks the numbers with `query`, validates the spec and HTML, publishes, and gives you a link such as `http://127.0.0.1:8484/d/sales-weekly`. [http://127.0.0.1:8484](http://127.0.0.1:8484) lists every dashboard.

A new dashboard is an uncertified **draft**. After reviewing it, the data team certifies it from the CLI, and the badge says who certified it and when. Any later change makes it a draft again.

```sh
uv run prumi list --config examples/demo-dbt-project/prumi.toml
uv run prumi certify sales-weekly 1 --config examples/demo-dbt-project/prumi.toml
```

The other commands are `uncertify`, `delete`, `requests` (metrics agents asked for that don't exist yet) and `validate` (check a spec file and its HTML).

**Your own dbt project:** see [Preparing your dbt project](packages/prumi-adapter-dbt/README.md#preparing-your-dbt-project) for what Prumi reads and how to declare display formats, then point a `prumi.toml` at its `target/semantic_manifest.json` and warehouse, like [the demo's](examples/demo-dbt-project/prumi.toml). This release connects to DuckDB only.

## Connect your agent

Prumi's MCP endpoint is `http://127.0.0.1:8484/mcp` (Streamable HTTP). With Claude Code:

```sh
claude mcp add --transport http prumi http://127.0.0.1:8484/mcp
```

Any MCP client that runs on your machine and supports Streamable HTTP can connect the same way. Clients that connect from their vendor's cloud (connectors configured in a web app, for example) can't reach a server on your machine.

The server tells the agent how to work with it: use only catalog metrics and ask for missing ones with `request_metric`, never write numbers into the page, declare totals and comparisons in the spec, and validate before publishing.

## Where your data goes

- **Dashboard data:** warehouse → Prumi → browser. It never passes through the agent or its LLM.
- **`query` results:** warehouse → Prumi → the agent → the agent's LLM provider. That's why `query` returns at most 200 rows.
- **What Prumi stores:** specs, HTML, certifications and metric requests, in `registry.sqlite3` under the config's `data_dir` (`.prumi` by default). No query results, and no row data or filter values in logs.

Every number on a dashboard is computed by the source at the grain it's shown: Prumi never re-aggregates ratios, averages or distinct counts. The dashboard's HTML runs in a sandboxed frame that can't call any server, and the badge and data freshness come from Prumi, not from the HTML.

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

```sh
uv sync
uv run pytest        # builds the demo project on first use
uv run ruff check
uv run ruff format
```

The browser tests drive headless Chromium and skip when it isn't installed. To run them: `uv run playwright install --with-deps chromium`.

## License

[Apache 2.0](LICENSE)
