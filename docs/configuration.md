# Configuration

Prumi reads one file, passed with `--config` (`prumi serve --config prumi.toml`). Relative paths in it resolve against the folder the file is in. An unknown section or key stops the server with a message that names it.

```toml
[server]
locale = "en-US"

[source]
adapter = "dbt"
project = "analytics"

[source.dbt]
semantic_manifest = "my-dbt-project/target/semantic_manifest.json"

[source.dbt.connection]
type = "duckdb"
path = "warehouse.duckdb"
```

The [demo's config](../examples/demo-dbt-project/prumi.toml) is a working example, but it pins `as_of` to a date: leave that out for your own data.

## `[server]`

Every key is optional.

| Key | Default | What it does |
|---|---|---|
| `host` | `127.0.0.1` | The address Prumi listens on. It must be a loopback address (`127.0.0.1`, `::1` or `localhost`): this release has no login, so anything else would expose the warehouse to the network. |
| `port` | `8484` | The port. The MCP endpoint is `http://<host>:<port>/mcp`. |
| `data_dir` | `.prumi` | Where the registry (`registry.sqlite3`) is kept. Use a local disk: SQLite's WAL mode doesn't work over a network filesystem. |
| `locale` | `en-US` | The display locale, a tag such as `en-US`, `pt-BR` or `de-DE`. Every viewer sees numbers and dates formatted this way, whatever their browser's language. |
| `as_of` | today | Pins "today" to a date (`2026-10-15`), so time windows and partial periods are computed as of that day. For demos and tests. |
| `script_hosts` | `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `unpkg.com` | The hosts a dashboard may load scripts from. Validation warns about a script URL with no exact version. |
| `style_hosts` | the same, plus `fonts.googleapis.com` | The hosts a dashboard may load stylesheets from. |
| `font_hosts` | the same, plus `fonts.gstatic.com` | The hosts a dashboard may load fonts from. |

## `[source]`

| Key | Default | What it does |
|---|---|---|
| `adapter` | required | The adapter that reads your metrics. This release has one: `dbt`. |
| `project` | none | A name for people: the home page shows it in its header. |

The adapter's own settings go in `[source.<adapter>]`.

## `[source.dbt]`

| Key | What it does |
|---|---|
| `semantic_manifest` | The path to your dbt project's `target/semantic_manifest.json`, which `dbt parse` writes. Prumi reads only this file; see [Preparing your dbt project](../packages/prumi-adapter-dbt/README.md#preparing-your-dbt-project). |

### `[source.dbt.connection]`

| Key | What it does |
|---|---|
| `type` | The warehouse type. This release supports `duckdb`. |
| `path` | The DuckDB file dbt built. Prumi opens it read-only. |

Changes to the config or the manifest take effect when Prumi restarts.
