# Prumi

[![CI](https://github.com/prumihq/prumi/actions/workflows/ci.yml/badge.svg)](https://github.com/prumihq/prumi/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/prumi)](https://pypi.org/project/prumi/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

**AI dashboards with official numbers.** People build dashboards with their own AI agent and design them however they like. Prumi supplies every number from your semantic layer, checks the page before it's published, and lets the data team certify it.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/prumi-at-a-glance-dark.svg">
  <img alt="A Prumi dashboard has three owners: the design comes from your AI agent, every number from your semantic layer, and the certified badge from your data team" src="docs/images/prumi-at-a-glance-light.svg">
</picture>

**Want to see it first?** [Try it](#try-it) on a demo shop, with the usual BI traps planted in its data: one `docker run` line, no project of your own needed.

> [!NOTE]
> Prumi is an early 0.x release. It runs on one machine, for one person, with dbt on DuckDB or Postgres. There's no login yet, so it can't be shared with a team. See [Status and roadmap](#status-and-roadmap).

## Try it

With Docker running, start Prumi on the demo shop:

```sh
docker run -p 127.0.0.1:8484:8484 ghcr.io/prumihq/prumi-demo
```

In another terminal, connect your agent (here Claude Code), then start a new agent session:

```sh
claude mcp add --scope user --transport http prumi http://127.0.0.1:8484/mcp
```

Ask it: *"Build a dashboard of weekly revenue by region, with average order value."* The [demo page](examples/demo-dbt-project/README.md#try-prumi-on-it) says what's in the shop, what else to ask, how to certify a dashboard, and how to try it without Docker.

Keep the `127.0.0.1:` in `-p`: it makes Prumi reachable from your machine only, and Prumi has no login yet ([why](docs/configuration.md#running-in-a-container)).

## Why Prumi

People already build dashboards with AI: paste a spreadsheet into a chat, and a good-looking page appears in minutes. The data team can't stop it, and can't trust it. Prumi keeps the speed and fixes the numbers.

| | AI dashboards today | With Prumi |
|---|---|---|
| **The page** | Whatever the agent designs | Still whatever the agent designs: any HTML |
| **The numbers** | Worked out by the AI: averages of averages, a half month compared with a full one, totals that don't match their rows | Computed by your semantic layer at the grain shown. Prumi checks the [BI rules](docs/bi-rules.md) and refuses any number it didn't deliver ([R8](docs/bi-rules.md#r8-every-number-shown-is-a-value-prumi-delivered)) |
| **The data** | A frozen copy, pasted into the chat | Read from your warehouse, and never sent to the AI: it sees metric definitions and the shape of the data, not the values ([where your data goes](#where-your-data-goes)) |
| **The definitions** | Whatever the AI guessed revenue means | Your semantic layer's, never re-implemented. Prumi never decides who sees which rows |
| **Trust** | Nobody knows where it came from | A draft until the data team certifies a version. Any change makes it a draft again, and the page can't fake the badge |

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/how-it-works-dark.svg">
  <img alt="The AI agent talks to Prumi over MCP. Prumi sends metric queries to the semantic layer, gets the numbers from the warehouse, and serves the checked dashboard to the browser. The data team certifies it. The data never passes through the agent." src="docs/images/how-it-works-light.svg">
</picture>

1. The agent connects to Prumi over MCP, the open protocol AI agents use for tools. It finds the official metrics in Prumi's catalog and learns the shape of the data: row counts, label lengths, magnitudes.
2. It writes a dashboard: a small spec saying which metrics, dimensions and filters to show, plus any HTML.
3. Prumi checks the spec against the BI rules, renders the page with real data, checks every number, and publishes it as a draft.
4. Viewers open it in the browser, with data that goes warehouse → Prumi → browser.
5. The data team certifies it, and the badge shows who certified it and when.

## Use it with your metrics

Prumi reads metrics from your semantic layer through an adapter, one per source.

> [!TIP]
> Not on a supported source yet, or just looking? [Try it](#try-it) on the demo shop first: a dbt project on DuckDB, in one container.

| Source | Status |
|---|---|
| dbt Semantic Layer (MetricFlow), on DuckDB and Postgres | 🟢 Supported in 0.2 |
| dbt on Snowflake, BigQuery and Databricks | 🟡 Planned |
| Power BI semantic models | 🟡 Planned |

**Install** Prumi with the adapter for your source, and the headless Chromium it checks pages with:

```sh
uv tool install "prumi[dbt]"
prumi install-chromium   # add --with-deps on Linux if system libraries are missing
```

Prumi comes as Python packages: `prumi` is the server and the `prumi` command, and each source has its own adapter package, such as `prumi-adapter-dbt`. `prumi[dbt]` installs both; with pip, `pip install "prumi[dbt]"`.

**Configure.** Prepare your dbt project ([what Prumi reads, and how to declare display formats](packages/prumi-adapter-dbt/README.md#preparing-your-dbt-project)), then write a `prumi.toml`:

```toml
[source]
adapter = "dbt"

[source.dbt]
semantic_manifest = "my-dbt-project/target/semantic_manifest.json"

[source.dbt.connection]
type = "duckdb"
path = "warehouse.duckdb"
```

[docs/configuration.md](docs/configuration.md) lists every setting. One worth knowing: every viewer sees numbers formatted the same way, whatever their browser's language, set with `locale` under `[server]` (default `en-US`). Pages format numbers with `prumi.format` and change figures with `prumi.change`, and pass `prumi.locale` to their other `Intl` calls; validation refuses a page whose text changes with the browser's language.

Settings can also come from `PRUMI__…` environment variables (`PRUMI__SERVER__PORT` for `port` under `[server]`): a variable wins over the file, and with variables alone no `prumi.toml` is needed. A Postgres password never goes in the file: `password_env` names the variable that holds it ([settings from the environment](docs/configuration.md#settings-from-environment-variables)).

Dashboards and certifications are kept in the registry: by default a SQLite file in `data_dir`, which is fine on one machine. For a real install, keep it in a Postgres database of its own ([`[registry]`](docs/configuration.md#registry)).

**Run** Prumi, then [connect your agent](#connect-your-agent):

```sh
prumi serve --config prumi.toml
```

While it runs, the server picks up a new manifest a few seconds after `dbt parse` writes it, so you don't restart it for metric changes. Changes to `prumi.toml` still need a restart.

**Ask** your agent for a dashboard on your metrics. It replies with a link such as `http://127.0.0.1:8484/d/weekly-revenue-by-region`, and [http://127.0.0.1:8484](http://127.0.0.1:8484) lists every dashboard with its status.

**Review and certify.** A new dashboard is a draft. `prumi list` shows every dashboard and its status, `prumi certify <id> <version>` certifies a version after review, and `prumi uncertify <id>` withdraws it. `prumi requests` lists the metrics agents asked for that don't exist yet, `prumi validate` checks a spec file and its HTML, and `prumi delete` removes a dashboard. Each command takes `--config prumi.toml`. `prumi --version` prints the installed version.

## Connect your agent

Prumi's MCP endpoint is `http://127.0.0.1:8484/mcp` (Streamable HTTP). With Claude Code:

```sh
claude mcp add --scope user --transport http prumi http://127.0.0.1:8484/mcp
```

`--scope user` makes Prumi available in every folder. Without it, Claude Code registers the server only for the folder you ran the command in.

This release is tested with Claude Code. Any MCP client that runs on your machine and supports Streamable HTTP should connect the same way. Clients that connect from their vendor's cloud (connectors configured in a web app, for example) can't reach a server on your machine.

The server tells the agent how to work with it: build dashboards with Prumi rather than the client's own dashboard or artifact tool, use only catalog metrics and ask for missing ones with `request_metric`, never write numbers into the page, declare totals and comparisons in the spec, and validate before publishing. Validation also reports layout problems, such as overlapping or clipped text, by element path and never with the page's text. The tools:

| Tool | For |
|---|---|
| `authoring_guide` | The spec format, the page API (`prumi.*`), the result object and a working example. Agents read it once before writing. |
| `find_metrics`, `describe_metric` | Finding the official metrics and the dimensions each one can be split by. |
| `query` | Learning the shape of the data while designing: rows, empties, label lengths, magnitudes. No values. |
| `validate_spec` | Checking a spec and its HTML, or edits to earlier content (`base` + `html_edits`), optionally in more filter states (`check_states`) and at phone width. Every result has a `candidate_id`; only a clean one can be published. |
| `publish_dashboard` | Publishing a clean `candidate_id` (or a spec and HTML) as a new draft version. |
| `list_dashboards`, `get_dashboard` | Reading back published dashboards: versions, spec, HTML, status, and what viewers' browsers flagged (element paths, never the numbers). |
| `request_metric` | Asking the data team for a metric that doesn't exist yet. |

## Where your data goes

- **Dashboard data:** warehouse → Prumi → browser. It never passes through the agent or its LLM.
- **What the agent sees:** Prumi never sends company data to your agent: `query` shows the shape of the data, and the numbers appear only in the dashboard. Its LLM provider gets metric definitions, the shape of results (row counts, empties, label lengths, magnitudes), issue codes and element paths: never a value or a name from the data.
- **What Prumi stores:** specs, HTML, certifications, metric requests and the check reports from viewers' browsers, in the registry: `registry.sqlite3` under the config's `data_dir` (`.prumi` by default), or the Postgres database `[registry.postgres]` names ([`[registry]`](docs/configuration.md#registry)). Check reports keep issue codes, element paths and counts, never numbers or names from the data. No query results, and no row data or filter values in logs.

The dashboard's HTML runs in a sandboxed frame that can't call any server, and the badge and data freshness come from Prumi, not from the HTML.

## Status and roadmap

Prumi proves the whole loop on one machine: an agent finds the official metrics, builds and publishes a dashboard, a browser shows it with checked numbers, and the data team certifies it. It doesn't have login or sharing yet (it only listens on your own machine), and it connects to DuckDB and Postgres only. The [changelog](CHANGELOG.md) lists what each release contains.

Known limitations:
- Installed with `uv`, the server needs headless Chromium (`prumi install-chromium`) and won't start without it. The container images carry it.
- Canvas charts aren't allowed: their text can't be checked.
- No login: every viewer is the local user, and the warehouse connection (DuckDB or Postgres) is a shared account.
- One source: dbt on DuckDB and Postgres only, with the legacy `measures:` YAML.
- `metric_time` is the only time dimension, with the standard calendar.
- Comparisons (`compare`) only work on blocks without a time grain, and `total` and `others` carry no comparison values.
- One display locale per server: every viewer sees numbers formatted the same way, whatever their browser's language.
- Tested with Claude Code only. Clients that connect from their vendor's cloud can't reach a server on your machine.
- The container images are tested with Docker Engine on Linux only.
- One server per registry: several Prumi servers behind one URL aren't supported. A new Postgres registry starts empty: nothing is copied from an existing SQLite one.

Where it's going, in this order:
- **Team use:** login and sharing with a team, `docker compose`, dbt on Snowflake, BigQuery and Databricks, and fiscal calendars.
- **Power BI:** an adapter for Power BI semantic models.

The [milestones](https://github.com/prumihq/prumi/milestones) hold what's scheduled for each release, issue by issue.

Releases stay 0.x until the dashboard spec, the adapter protocol and the MCP tools are stable.

## Troubleshooting

- **The agent has no Prumi tools** (it may ask you for a CSV instead): start Prumi before the agent session, because agents connect to their MCP servers when a session starts. In Claude Code, `/mcp` shows Prumi's state and reconnects it. If Prumi was added without `--scope user`, it's registered only in the folder where you ran `claude mcp add`.
- **Restart your agent sessions after upgrading Prumi.** An open session keeps the tool list and the instructions it loaded when it started.
- **`prumi serve` stops at startup:** the message names what to fix: Chromium isn't installed (`prumi install-chromium`, or `uv run prumi install-chromium` in a clone; add `--with-deps` on Linux if system libraries are missing), the semantic manifest is missing or unreadable (build the demo, or run `dbt parse` in your project), a table in the manifest doesn't exist on the connection, Prumi can't connect to Postgres or log in (the message says which setting to check: `host`, `port`, `dbname`, `user`, `sslmode`, `timezone` or `password_env`, and never shows the password), the variable `password_env` names is unset or empty, the manifest names a different Postgres database than `dbname`, a table exists but the Postgres role can't read it (grant it `SELECT`), `host` isn't a loopback address (in the container images any address is allowed, and `-p 127.0.0.1:…` keeps Prumi on your machine: see [Running in a container](docs/configuration.md#running-in-a-container)), `locale` isn't a tag the browser knows, or the registry can't be used (on Postgres the message names the `[registry.postgres]` setting or `PRUMI_REGISTRY_PASSWORD` to check, or says the database must be empty and in UTF8, the role must be able to create tables, or `prumi[postgres]` is missing: see [`[registry.postgres]`](docs/configuration.md#registrypostgres)).
- **A new manifest doesn't show up:** a manifest that fails a check while the server runs (it doesn't parse, or names a table that doesn't exist) is logged in the `prumi serve` output and the old one stays in use. Prumi tries it again every 30 seconds, so fixing the problem is enough.

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) explains how to set up the repository, run the tests and find your way around the code.

## Security

Please report vulnerabilities privately, as [SECURITY.md](SECURITY.md) describes.

## License

[Apache 2.0](LICENSE)
