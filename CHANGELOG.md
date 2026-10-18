# Changelog

All notable changes to Prumi. Versions stay 0.x until the maintainer says otherwise.

## 0.1.0 — unreleased

The first release: it proves the core loop on one machine, for one local user, against a demo dbt project on DuckDB ([MVP spec](specs/mvp/spec.md)). An agent connected over MCP finds the official metrics, explores them, writes a dashboard and publishes it; a browser shows it with every number computed by the source; the data team certifies it from the CLI, and any later edit makes it a draft again.

### Added

- **`prumi-core`**: the server and the `prumi` CLI.
  - MCP endpoint at `/mcp` (Streamable HTTP, stateless) with seven tools: `authoring_guide` (the spec format, the page API and a working example), `find_metrics`, `describe_metric`, `query` (capped at 200 rows for agents), `validate_spec`, `publish_dashboard` (by the `candidate_id` of a successful validation, so the HTML is sent once) and `request_metric`.
  - Dashboard spec format version 0, with its JSON Schema.
  - Validator: spec rules, BI rules R2 to R8 and R10, HTML checks, and a dry run of every block.
  - Engine: blocks with time windows aligned to whole periods (`as_of`, partial periods, comparisons), totals and "Others" computed by the source, freshness, and a 5,000-row cap.
  - Registry on SQLite: immutable versions, fingerprints, and certification status computed on every read.
  - Hosting: each dashboard runs in a sandboxed frame (CSP, no network access), inside Prumi's shell with the trust badge and data freshness.
  - Query logs with origin, viewer, duration and row count, never values.
  - CLI: `serve`, `list`, `certify`, `uncertify`, `delete`, `requests`, `validate`.
- **`prumi-adapter-dbt`**: reads `target/semantic_manifest.json`. MetricFlow 0.213.0 compiles (exact pin, Apache-2.0); Prumi runs the SQL over its own DuckDB connection. Formats come from `config.meta.prumi`, and startup checks cover the manifest, the connection type and every relation.
- **`prumi-runtime`**: `runtime.js` (the `prumi.*` API in the dashboard frame, with no aggregation helpers) and `shell.js`, plain JS with no build step. The `prumi-core` wheel carries a copy of both.
- **Demo dbt project** (`examples/demo-dbt-project`): a deterministic synthetic shop on DuckDB, with metrics that plant the usual BI traps.

### Known limitations

- No login. The server only listens on loopback addresses, and every viewer is the local user; the DuckDB connection is a shared account.
- One source, dbt on DuckDB only, with the legacy `measures:` YAML.
- `metric_time` is the only time dimension, with the standard calendar.
- Manifest changes need a restart.
