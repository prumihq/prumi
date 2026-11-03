# Changelog

All notable changes to Prumi. Versions stay 0.x until the maintainer says otherwise. Entries come from pull request titles, through release-please.

## [0.2.0](https://github.com/prumihq/prumi/compare/v0.1.1...v0.2.0) (2026-11-03)


### Features

* prumi --version prints the installed version ([#23](https://github.com/prumihq/prumi/issues/23)) ([2ca671b](https://github.com/prumihq/prumi/commit/2ca671b49da7bf621cd7b6499293a40924cbd36e))
* reload the dbt manifest without restarting the server ([#25](https://github.com/prumihq/prumi/issues/25)) ([788d83c](https://github.com/prumihq/prumi/commit/788d83cf6f85d04d78d18af57be721e1a6edd311))
* run dbt projects on Postgres ([#21](https://github.com/prumihq/prumi/issues/21)) ([7d04546](https://github.com/prumihq/prumi/commit/7d04546346b68bad757403214f3526344ddcca3f))


### Bug Fixes

* reload a rewritten manifest on filesystems with coarse timestamps, and don't hang shutdown on a stuck reload ([#27](https://github.com/prumihq/prumi/issues/27)) ([e71b49f](https://github.com/prumihq/prumi/commit/e71b49fcdf77fb501048d61fca873cf8984bcc22))

## [0.1.1](https://github.com/prumihq/prumi/compare/v0.1.0...v0.1.1) (2026-11-02)


### Bug Fixes

* keep the dashboard title readable on phones when the badge is long ([#13](https://github.com/prumihq/prumi/issues/13)) ([b5d682d](https://github.com/prumihq/prumi/commit/b5d682da1f71dcc7a33ab11d64b92ab6a5b00596))

## [0.1.0](https://github.com/prumihq/prumi/releases/tag/v0.1.0) (2026-10-19)

The first release: it proves the core loop on one machine, for one local user, against a demo dbt project on DuckDB ([MVP spec](https://github.com/prumihq/prumi/blob/v0.1.0/specs/mvp/spec.md)). An agent connected over MCP finds the official metrics, explores them, writes a dashboard and publishes it; a browser shows it with every number computed by the source; the data team certifies it from the CLI, and any later edit makes it a draft again.

### Features

* **Installable from PyPI** as `prumi` and `prumi-adapter-dbt`, released together with one version: `prumi[dbt]` installs both.
* **`prumi`**: the server and the `prumi` CLI. Its extra `dbt` installs the dbt adapter with it (`prumi[dbt]`); without an adapter, `prumi serve` stops and names the extra to install.
  * MCP endpoint at `/mcp` (Streamable HTTP, stateless) with nine tools: `authoring_guide` (the spec format, the page API and a working example), `find_metrics`, `describe_metric`, `query` (the shape of the data, never its values: no company data reaches the agent), `validate_spec` (also for edits to any validated candidate, failed ones included, or to a published version: `base` + `html_edits`; and for extra filter states to check: `check_states`), `publish_dashboard` (by the `candidate_id` of a successful validation, so the HTML is sent once), `list_dashboards`, `get_dashboard` and `request_metric`. The server's instructions walk the agent through that workflow, and tell it to build company dashboards with Prumi rather than with its client's own dashboard or artifact tool.
  * Dashboard spec format version 0, with its JSON Schema.
  * Validator: spec rules, BI rules R2 to R8 and R10, HTML checks, and a dry run of every block.
  * Number check (R8): validation renders the page in headless Chromium with the dry run's data, and every number it shows must be a value Prumi delivered to the block its element is bound to, tooltips included (a hover pass finds them). Values copied into the source, canvas, images, frames and closed shadow roots are refused. Viewers' browsers run the same check for other filter states and tell the viewer when it flags a number; the version is then marked `needs_attention`, and certification refuses it.
  * One display locale per server (`[server] locale`, default `en-US`). Numbers, dates and period words (`14 de out.`, `12 semanas`) are read strictly in that locale, and a page whose bound text changes with the browser's language (`toLocaleString()`, `Intl.*(undefined, …)`) is refused (`R8.browser_locale`): validation renders it a second time in another language to find out.
  * Layout warnings from the same render: overlapping, clipped or very small text, a page wider than the screen, a bound element that renders empty. Desktop width by default, phone width on request. They give element paths and boxes, never the page's text.
  * Engine: blocks with time windows aligned to whole periods (`as_of`, partial periods, comparisons), totals and "Others" computed by the source, freshness, and a 5,000-row cap.
  * Registry on SQLite: immutable versions, fingerprints, and certification status computed on every read. It also keeps the latest check report from viewers' browsers per version and filter state, reduced to issue codes, element paths and counts: no numbers or names from the data.
  * Hosting: each dashboard runs in a sandboxed frame (CSP, no network access), inside Prumi's shell with the trust badge and data freshness, with a default light and dark theme. The shell's bar fits a phone screen.
  * Home page at `/`: the connected source and how recent its data is, a card for each dashboard with its status, version, update time and metrics, and the open metric requests. A search box filters the cards by title, id or metric, and with no dashboards yet the page shows the connect command with a copy button. It shows no metric values. The optional `[source] project` in `prumi.toml` names the project in its header.
  * Query logs with origin, viewer, duration and row count, never values.
  * CLI: `serve`, `list`, `certify`, `uncertify`, `delete`, `requests`, `validate`, and `install-chromium` (`--with-deps`), which runs Playwright's installer with Prumi's own Python, so it works after `uv tool install`.
* **`prumi-adapter-dbt`**: reads `target/semantic_manifest.json`. MetricFlow 0.213.0 compiles (exact pin, Apache-2.0); Prumi runs the SQL over its own DuckDB connection. Formats come from `config.meta.prumi`, and startup checks cover the manifest, the connection type and every relation.
* **`prumi-runtime`**: `runtime.js` (the `prumi.*` API in the dashboard frame, with no aggregation helpers; `prumi.format` and `prumi.change` write numbers and change figures in the server's locale), `shell.js`, `provenance.js` (the number matcher), `theme.css`, `home.css` and `home.js`, plain JS and CSS with no build step. The `prumi` wheel carries a copy of each.
* **Demo dbt project** (`examples/demo-dbt-project`): a deterministic synthetic shop on DuckDB, with metrics that plant the usual BI traps.
