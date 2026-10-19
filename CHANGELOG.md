# Changelog

All notable changes to Prumi. Versions stay 0.x until the maintainer says otherwise.

## 0.1.0 — unreleased

The first release: it proves the core loop on one machine, for one local user, against a demo dbt project on DuckDB ([MVP spec](specs/mvp/spec.md)). An agent connected over MCP finds the official metrics, explores them, writes a dashboard and publishes it; a browser shows it with every number computed by the source; the data team certifies it from the CLI, and any later edit makes it a draft again.

### Added

- **`prumi-core`**: the server and the `prumi` CLI.
  - MCP endpoint at `/mcp` (Streamable HTTP, stateless) with nine tools: `authoring_guide` (the spec format, the page API and a working example), `find_metrics`, `describe_metric`, `query` (the shape of the data, never its values: no company data reaches the agent), `validate_spec` (also for edits to any validated candidate, failed ones included, or to a published version: `base` + `html_edits`; and for extra filter states to check: `check_states`), `publish_dashboard` (by the `candidate_id` of a successful validation, so the HTML is sent once), `list_dashboards`, `get_dashboard` and `request_metric`.
  - Dashboard spec format version 0, with its JSON Schema.
  - Validator: spec rules, BI rules R2 to R8 and R10, HTML checks, a dry run of every block, and the number check: the page is rendered in headless Chromium with the dry run's data, and every number it shows must be a value Prumi delivered to the block its element is bound to, tooltips included (R8). Values copied into the source, images, frames and closed shadow roots are refused. Numbers are read strictly in the server's locale, and a page whose bound text changes with the browser's language (`toLocaleString()`, `Intl.*(undefined, …)`) is refused (`R8.browser_locale`): validation renders it a second time in another language to find out. Viewers' browsers run the same check for other filter states and tell the viewer when it flags a number; the version is then marked `needs_attention`, and certification refuses it.
  - Engine: blocks with time windows aligned to whole periods (`as_of`, partial periods, comparisons), totals and "Others" computed by the source, freshness, and a 5,000-row cap.
  - Registry on SQLite: immutable versions, fingerprints, and certification status computed on every read.
  - Hosting: each dashboard runs in a sandboxed frame (CSP, no network access), inside Prumi's shell with the trust badge and data freshness, with a default light and dark theme.
  - Home page at `/`: the connected source and how recent its data is, a card for each dashboard with its status, version, update time and metrics, and the open metric requests. A search box filters the cards by title, id or metric, and with no dashboards yet the page shows the connect command with a copy button. It shows no metric values. The optional `[source] project` in `prumi.toml` names the project in its header.
  - Query logs with origin, viewer, duration and row count, never values.
  - CLI: `serve`, `list`, `certify`, `uncertify`, `delete`, `requests`, `validate`.
- **`prumi-adapter-dbt`**: reads `target/semantic_manifest.json`. MetricFlow 0.213.0 compiles (exact pin, Apache-2.0); Prumi runs the SQL over its own DuckDB connection. Formats come from `config.meta.prumi`, and startup checks cover the manifest, the connection type and every relation.
- **`prumi-runtime`**: `runtime.js` (the `prumi.*` API in the dashboard frame, with no aggregation helpers), `shell.js`, `provenance.js` (the number matcher), `theme.css`, `home.css` and `home.js`, plain JS and CSS with no build step. The `prumi-core` wheel carries a copy of each.
- **Demo dbt project** (`examples/demo-dbt-project`): a deterministic synthetic shop on DuckDB, with metrics that plant the usual BI traps.

### Known limitations

- The server needs headless Chromium (`uv run playwright install chromium`) and won't start without it.
- Canvas charts aren't allowed: their text can't be checked.

- No login. The server only listens on loopback addresses, and every viewer is the local user; the DuckDB connection is a shared account.
- One source, dbt on DuckDB only, with the legacy `measures:` YAML.
- `metric_time` is the only time dimension, with the standard calendar.
- Manifest changes need a restart.
- One display locale per server (`[server] locale`, default `en-US`): every viewer sees numbers formatted the same way, whatever their browser's language.
