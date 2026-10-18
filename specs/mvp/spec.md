# MVP: prove the core loop on one machine

Release: 0.1.0 · Status: accepted

## Goal

An AI agent connected to Prumi over MCP finds the metrics in a dbt project, explores them, writes a dashboard (spec + any HTML), validates it and publishes it. A browser opens the dashboard: every number is computed by the source at the grain it's shown, and the trust badge and data freshness come from the server, not from the HTML. An admin certifies the dashboard from the CLI; any later edit revokes the certification.

Everything runs on one machine, for one local user, against a demo dbt project on DuckDB. The loop has to work end to end and be easy to show; it doesn't have to be installable by a company yet.

## Context

- [ADR 0001](../../docs/adr/0001-repository-layout.md): repository layout (uv workspace, one package per adapter, plain-JS runtime).
- [ADR 0002](../../docs/adr/0002-dbt-adapter-metricflow-compiles.md): MetricFlow compiles, Prumi executes; the engine owns time windows.
- [Spike `metricflow-compile`](../../spikes/metricflow-compile/README.md): evidence for ADR 0002, the catalog metadata MetricFlow exposes, and its caveats. Its checks become the adapter's contract test.
- [BI rules](../../docs/bi-rules.md): what R1–R10 mean, how each is enforced, and its issue codes.

### The system in one picture

```
 Agent (Claude Code, Cursor, ...)            Browser
        │ MCP over HTTP                         │ HTTP
┌───────▼───────────────────────────────────────▼──────────────┐
│ prumi serve  (one process, 127.0.0.1:8484)                   │
│                                                              │
│  /mcp          MCP tools ──┐                                 │
│  /d/<id>       shell page  │   /api/...  block data, status  │
│  /runtime.js               │                                 │
│                    ┌───────▼────────┐   ┌──────────────────┐ │
│                    │ engine +       │   │ registry         │ │
│                    │ validator      │   │ (SQLite)         │ │
│                    └───────┬────────┘   └──────────────────┘ │
│                    ┌───────▼────────┐                        │
│                    │ dbt adapter    │  MetricFlow compiles   │
│                    └───────┬────────┘  (no connection)       │
└────────────────────────────┼─────────────────────────────────┘
                             │ Prumi's own connection
                       DuckDB (demo warehouse)

 CLI: prumi certify / uncertify / delete / list / requests  (same data dir)
```

### Data flow (stated in the README, AC 30)

- **Dashboard data:** warehouse → Prumi → browser. It never passes through the agent or its LLM.
- **`query` results:** warehouse → Prumi → the agent → the agent's LLM provider. That's why `query` returns at most 200 rows.
- **What Prumi stores:** specs, HTML, certifications, metric requests. No query results, and no row data in logs.

## In scope

### 1. Packages and dependencies

| Package | Adds | Notes |
|---|---|---|
| `prumi-core` (import `prumi`) | `mcp` (official Python SDK), `starlette`, `uvicorn`, `pydantic>=2`, `pyyaml` | Never imports an adapter. Provides the `prumi` CLI entry point. |
| `prumi-adapter-dbt` (import `prumi_adapter_dbt`) | `metricflow==0.213.0` (exact pin); extra `duckdb` → `duckdb` | Registers itself under the entry point group `prumi.adapters` as `dbt`. No dependency on dbt-core. |
| `prumi-runtime` | none | `src/runtime.js`, plain JS, no build step. |

- Python 3.12. Versions other than the MetricFlow pin are whatever `uv lock` resolves at implementation time.
- The server serves `runtime.js` and `shell.js` from the `prumi-runtime` package. It has to work both in the workspace and from built wheels: the `prumi-core` wheel includes a copy of both at build time (hatch `force-include`). There is exactly one source of each.
- Dev-only: `pytest`, `ruff`, `playwright` (Python; browser tests run headless Chromium). Node isn't needed for anything.

### 2. Configuration

`prumi serve` reads `prumi.toml` (path given with `--config`; default `./prumi.toml`):

```toml
[server]
host = "127.0.0.1"          # loopback only in this milestone
port = 8484
data_dir = ".prumi"         # registry lives here
as_of = "2026-10-15"        # optional: pins "today" for windows and partial periods
script_hosts = ["cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com"]
style_hosts = ["fonts.googleapis.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com"]
font_hosts = ["fonts.gstatic.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com"]

[source]
adapter = "dbt"

[source.dbt]
semantic_manifest = "examples/demo-dbt-project/target/semantic_manifest.json"

[source.dbt.connection]
type = "duckdb"
path = "examples/demo-dbt-project/demo.duckdb"
```

- Relative paths resolve against the config file's folder.
- **The server refuses to start if `host` isn't a loopback address** (`127.0.0.1`, `::1`, `localhost`). There's no login in this milestone, so binding elsewhere would expose the warehouse to the network.
- `as_of` is a date. Without it, "today" is the server's local date. The demo config pins it so tests and the demo are reproducible.
- Exactly one source. Internally, sources are held in a mapping keyed by name (`"default"`), see [Design for later](#design-for-later).

### 3. Adapter protocol (`prumi.adapters`)

All types are immutable (frozen dataclasses or frozen pydantic models).

```python
class Adapter(Protocol):
    name: str
    def capabilities(self) -> Capabilities: ...
    def discover(self) -> Catalog: ...
    def relations(self, metrics: Sequence[str]) -> frozenset[str]: ...
    def execute(self, query: SemanticQuery, viewer: Viewer) -> Result: ...
```

- **`capabilities()`**: `identity_mode` (`"per_viewer"` | `"shared_account"`; the DuckDB connection declares `"shared_account"`), `time_grains` (`day, week, month, quarter, year`), `calendars` (`("standard",)`).
- **`discover()`**: the `Catalog`, read once at startup.
  - `Metric`: `name`, `label`, `description`, `kind` (`simple | ratio | derived | cumulative | conversion`), `aggregation` (for simple metrics: MetricFlow's agg type; else `None`), `additive: bool`, `format: Format`, `owner: str | None`, `filter: str | None` (the metric's own filter, display only).
  - `Dimension`: `name` (the query name, e.g. `customer__region`), `label`, `description`.
  - The only time dimension is `metric_time` (each metric's aggregation time dimension). Other time dimensions aren't exposed in this milestone.
- **`relations(metrics)`**: the names of the categorical dimensions valid for **all** the given metrics together, using the same names as the catalog (MetricFlow only follows declared entity relationships, which is what R2 relies on).
- **`execute(query, viewer)`**: compiles and runs one `SemanticQuery` as `viewer`. Synchronous; the server calls it in a worker thread.

```python
@dataclass(frozen=True)
class SemanticQuery:
    metrics: tuple[str, ...]          # empty only for a dimension-values query (see below)
    dimensions: tuple[str, ...]       # categorical dimension names
    time_grain: Grain | None          # group by metric_time at this grain
    window: DateRange | None          # inclusive dates, already aligned by the engine
    filters: tuple[Filter, ...]
    order_by: tuple[OrderBy, ...]     # field = a metric, a dimension or "metric_time"
    limit: int | None

@dataclass(frozen=True)
class Filter:
    dimension: str
    op: Literal["eq", "ne", "in", "not_in", "lt", "le", "gt", "ge"]
    values: tuple[str | int | float | bool | None, ...]

@dataclass(frozen=True)
class Viewer:
    id: str
    security_context: Mapping[str, str]

LOCAL_VIEWER = Viewer(id="local", security_context={})
```

- **`Result`**: `columns` (name, kind `dimension | time | metric`), `rows` (tuples in column order), `row_count`. Column names are the semantic names: dimension names, `metric_time`, metric names. Values: dates as `date`, numbers as `int` or `float` (`Decimal` is converted to `float`), `None` for NULL.
- **Dimension values query:** `metrics=()` with exactly one dimension and no `time_grain` returns that dimension's distinct values (used for filter pickers). It runs as the viewer like any other query.
- Filter semantics, defined here so every adapter behaves the same:
  - `eq` / `in` with `None` among the values also match NULL. `ne` / `not_in` **keep** NULL rows unless `None` is among the values. (Needed for "Others" in R6: a NULL region must land in Others, not disappear.)
  - `in` / `not_in` need at least one value; the others need exactly one. `lt`, `le`, `gt` and `ge` reject `None`.
  - `execute` rejects dimension names that the catalog doesn't show for the query's metrics (e.g. a longer join route MetricFlow would accept), so the adapter and the validator's R2 check agree.
- Adapters are discovered through the `prumi.adapters` entry point group; core selects one by `source.adapter`. The entry point is a factory `(config: Mapping[str, Any], config_dir: Path) -> Adapter`, called with the adapter's own table (`[source.<name>]`) and the folder that relative paths resolve against.

### 4. dbt adapter

- **One module, `prumi_adapter_dbt/_metricflow.py`, holds every `metricflow` / `metricflow_semantics` import.** A test fails if any other module imports them.
- **Loading:** `parse_manifest_from_dbt_generated_manifest(text)` → `SemanticManifestLookup` → `MetricFlowEngine(lookup, CompileOnlySqlClient(dialect))`, as in the spike. The compile-only client provides `sql_engine_type` and `sql_plan_renderer` for the connection's dialect, and raises on `query`, `execute` and `dry_run`.
- **Compiling:** `engine.explain(MetricFlowQueryRequest.create(...)).sql_statement`. The adapter refuses SQL with bind parameters (the spike saw none). It never calls `engine.query(...)`.
- **Executing:** `prumi_adapter_dbt/connections/duckdb.py` opens `duckdb.connect(path, read_only=True)` for each `execute` call and closes it afterwards. Results are read **by column name** (MetricFlow reorders columns; spike caveat 5) and renamed to the semantic names (`metric_time__month` → `metric_time`).
- **Startup checks** (each raises `prumi.adapters.AdapterStartupError`, which the server and CLI print as a plain message naming the file or relation and what to do):
  1. The manifest file exists and MetricFlow can parse it.
  2. The configured connection type has a MetricFlow dialect.
  3. Every semantic model's relation (database.schema.table, as dbt resolved it) exists on the connection (`SELECT 1 FROM <relation> LIMIT 0`). This is the "names are baked into the SQL" caveat from ADR 0002: for DuckDB, the database name is the file name without extension, so a renamed file fails here instead of at the first query.
- **Mapping a `SemanticQuery` to MetricFlow:**
  - `time_grain` → group by `metric_time__<grain>`.
  - `window` → a where clause on `{{ TimeDimension('metric_time', 'day') }}` with `>= start` and `<= end`. This is the form the spike's check 4b verified, and it works with or without a time group-by (KPIs). `time_constraint_start/end` aren't used.
  - `filters` → where clauses on `{{ Dimension('<name>') }}`, rendered by the adapter:
    - strings in single quotes, with `'` doubled;
    - **a string containing `{` or `}` is rejected** (`ValueError`), because MetricFlow renders where clauses as Jinja and the value would be a template;
    - ints and finite floats via `repr`; booleans as `TRUE` / `FALSE`; `None` as described in the filter semantics above (`IS NULL` / `IS NOT NULL` combined with `OR` / `AND`);
    - dimension names must be plain identifiers, because they're inserted into the template too.
  - `order_by` / `limit` → MetricFlow's `order_by_names` (with `-` for descending) and `limit`.
  - dimension-values query → MetricFlow's dimension-values path (`explain_get_dimension_values` or a group-by-only request, whichever the contract test shows compiles without running anything).
- **Catalog extraction** (from `list_metrics`, `list_dimensions`, `list_group_bys`):
  - **Aggregation as declared:** MetricFlow rewrites some aggregations while parsing (`count` becomes a sum of 1s, `median` a percentile). The catalog reports the aggregation written in the dbt project, read from the manifest before that rewrite, and the additivity rule uses it.
  - **Additivity:** a metric is `additive` only if it's `simple`, its aggregation is `sum`, `count` or `sum_boolean`, and its measure has no `non_additive_dimension`. Everything else is non-additive: ratio, derived (even `a - b`), cumulative, conversion, `count_distinct`, `average`, `min`, `max`, `median`, `percentile`, and semi-additive measures. Being wrong about "additive" gives wrong totals; being wrong about "non-additive" only costs an extra query.
  - **Format (R10):** read from `config.meta.prumi`:
    ```yaml
    config:
      meta:
        prumi:
          format: currency   # number | integer | percent | currency
          unit: USD          # ISO 4217 code for currency; free text otherwise ("orders")
          decimals: 2        # optional
        owner: sales-analytics
    ```
    `percent` means the value is a fraction (0.25 shows as 25%). If `format` is missing or invalid, or `currency` has no 3-letter unit, the format is `number` with no unit and the catalog records a warning.
  - **Owner:** `config.meta.owner` if it's a string.
  - Dimensions: categorical dimensions reachable from at least one metric, plus `metric_time`.
  - **One name per attribute: the shortest join path.** MetricFlow lists a dimension once per valid join path (`customer__region` directly, and `order__customer__region` through orders, for a metric on order lines). The adapter groups names by their final entity and dimension (`customer` + `region`) and keeps only the name with the fewest entity links for each metric. Names of equal length both stay, because Prumi can't tell which route is meant. Nothing reachable is lost: if region is only reachable through two hops, the two-hop name is the one shown. The catalog, `relations()` and `describe_metric` use the same rule, and names it drops are rejected like any unknown dimension (R2).
- **Manifest changes need a restart.** No reload in this milestone.

### 5. Time (`prumi.time`)

Grains: `day`, `week` (ISO: Monday to Sunday), `month`, `quarter`, `year`. Standard calendar only.

**Today and partial periods.** `as_of` is "today". A period is **complete** if its last day is before `as_of`; the period containing `as_of` is the **current, partial** period. Wherever the current period is included (`include_current`, `to_date`, explicit dates), it runs through `as_of − 1 day`: `as_of` itself has no complete data, and including it would raise `R3.stale_data` on every block.

**Window forms** (used by time filters, fixed block windows and `query`):

| Form | Meaning |
|---|---|
| `{last: N, grain: G}` | The N complete periods of G before the current one. **The default form.** |
| `{last: N, grain: G, include_current: true}` | Same, plus the current partial period (flagged). |
| `{to_date: G}` | From the start of the current period of G to `as_of − 1 day` (flagged partial). Empty on the first day of a period: the result is empty, with warning `R3.empty_window`. |
| `{start: D1, end: D2, grain: G?}` | Inclusive dates. Snapped **inward** to whole periods of G (default: the block's `time_grain`, else `day`). Periods that aren't complete are dropped unless `include_current: true`. If snapping changes the dates: warning `R3.window_snapped`. If nothing is left: error `R3.window_too_short`. |

N is 1 to 400.

**Grain compatibility (R4).** When a block groups by time, its `time_grain` must divide the window's periods exactly: the same grain, or `day`, or `month` in a `quarter` / `year` window, or `quarter` in a `year` window. Weeks only fit week windows. Anything else is error `R4.grain_mismatch`. Without this rule, the first and last group-by periods would silently hold partial data.

**Comparisons (R4).** `compare` is allowed only on blocks **without** `time_grain` (KPIs and by-dimension tables). Time series with a comparison series are out of scope.

| `compare` | Window form | Previous window |
|---|---|---|
| `previous_period` | `last` / explicit | The same number of periods immediately before. |
| `previous_period` | `to_date` | The same day offsets in the previous period, with the end clamped to that period's end (Mar 1–30 → Feb 1–28). |
| `previous_year` | any | Day and week grains: shifted 364 days (keeps weekdays aligned). Month, quarter and year grains: the same calendar periods one year earlier. `to_date`: the same dates one year earlier (Feb 29 → Feb 28). |

`include_current: true` together with `compare` is error `R3.partial_in_comparison`: a partial period is only compared through `to_date`, which aligns both sides.

The previous window runs as a separate query with the same metrics, dimensions and filters. Rows are matched on their dimension values. For each metric the engine adds `previous`, `delta = value − previous`, and `delta_pct = delta / |previous|` (`null` if `previous` is 0 or null). These are the only values the engine computes besides "Others" (R1).

**Freshness.** A metric's `data_through` is the latest `metric_time` day with data: the engine runs `SemanticQuery(metrics=(m,), time_grain=day, order_by=metric_time desc, limit=1)` as the viewer, with no filters. If `data_through` is earlier than the last day of a block's window: warning `R3.stale_data` ("data through 2026-10-10; the window ends 2026-10-11, so the last period may be incomplete"). Partial periods are decided by the calendar (`as_of`), not by freshness, because sparse metrics (no orders on Sundays) would otherwise look partial.

### 6. Dashboard spec, format version 0 (`prumi.spec`)

Pydantic models with `extra="forbid"`. The JSON Schema generated from them is committed at `packages/prumi-core/src/prumi/spec/dashboard-spec.v0.schema.json`, and a test fails if it's out of date. MCP tools take the spec as JSON; the CLI also accepts YAML.

```yaml
spec_version: 0
dashboard:
  id: sales-weekly                 # ^[a-z0-9][a-z0-9-]{0,62}$
  title: Sales, weekly

filters:
  - id: period
    type: time
    default: {last: 12, grain: week}
    calendar: standard             # optional; the only accepted value in this milestone
  - id: region
    type: dimension
    dimension: customer__region
    default: []                    # list of values; empty = all

data:
  - id: kpi_revenue
    title: Revenue                 # optional, informational
    metrics: [revenue, average_order_value]
    filters: [period, region]      # required; may be []
    compare: previous_year

  - id: revenue_by_region
    metrics: [revenue, gross_margin_pct]
    dimensions: [customer__region]
    filters: [period]              # explicit: the region filter does NOT apply here
    sort: {by: revenue, descending: true}
    top_n: 5
    others: true
    total: true

  - id: weekly_trend
    metrics: [revenue]
    time_grain: week
    dimensions: [channel]          # optional: one series per channel
    filters: [period, region]

  - id: monthly_trend
    metrics: [revenue]
    time_grain: month
    window: {last: 24, grain: month}   # a fixed window instead of a time filter
    filters: [region]

interactions:
  - from: revenue_by_region
    on: click
    sets_filter: region

metric_requests: [3]               # optional: ids returned by request_metric

presentation:
  bindings:                        # "#element-id" → block id
    "#kpi": kpi_revenue
    "#bars": revenue_by_region
    "#trend": weekly_trend
    "#monthly": monthly_trend
```

Block fields: `id` (slug, unique across filters and blocks), `title?`, `metrics` (1–10), `dimensions` (0–3 categorical), `time_grain?`, `filters` (**required**), `window?`, `compare?` (`previous_period | previous_year`), `sort?` (`by`: a metric, dimension or `metric_time`; `descending`), `top_n?` (1–100), `others?`, `total?`.

- **Status is not part of the spec.** Draft or certified is registry state, shown by the server.
- A dimension filter's value is a list of values (an `in` filter); `[]` means no filter; `null` in the list matches NULL.
- The HTML isn't in the spec: tools take `spec` and `html` side by side.

### 7. Validator (`prumi.validate`)

`validate(spec, html | None, catalog, metric_request_ids) -> Report`. A report holds `errors` and `warnings`, each an `Issue`:

```json
{"code": "R6.others_requires_top_n", "severity": "error",
 "path": "data[1].others", "message": "...", "hint": "..."}
```

`path` uses the spec's field names and list indexes. Messages are written for the agent: say what's wrong and how to fix it. Static checks run first; if they pass, the validator **dry-runs every block** with the filters' default values and reports failures as `query.failed`. **Warnings in the dry-run block results** (`R3.window_snapped`, `R3.empty_window`, `R3.stale_data`, `result.truncated`) are reported too, at `data[i]`, so the agent sees them before publishing.

| Code | Severity | Rule |
|---|---|---|
| `spec.invalid` | error | Schema violation (one issue per pydantic error). |
| `spec.duplicate_id` | error | Two filters or blocks share an id. |
| `spec.unknown_filter` / `spec.unknown_block` | error | A reference to an id that doesn't exist. |
| `spec.unsupported_calendar` | error | `calendar` other than `standard`. |
| `R2.invalid_dimension` | error | A block dimension isn't in `relations(block.metrics)`. |
| `R2.filter_dimension_invalid` | error | A block lists a dimension filter whose dimension isn't valid for all of the block's metrics. |
| `R3.no_window` | error | A block has neither a time filter in `filters` nor a fixed `window`. "All time" would silently include the current partial period. |
| `R3.window_too_short` / `R3.partial_in_comparison` | error | See [Time](#5-time-prumitime). |
| `R4.grain_mismatch` / `R4.compare_with_time_grain` | error | See [Time](#5-time-prumitime). |
| `R5.multiple_windows` | error | A block lists more than one time filter, or a time filter and a fixed `window`. |
| `R6.top_n_shape` | error | `top_n` without exactly one dimension, with `time_grain`, or without `sort` by a metric. |
| `R6.others_requires_top_n` | error | `others` without `top_n`. |
| `R6.total_requires_grouping` | error | `total` on a block without dimensions or `time_grain`. |
| `R7.unknown_metric` | error | Not in the catalog. The message gives up to 3 close names (difflib), and the hint says to call `request_metric` instead of inventing a metric. |
| `R7.unknown_metric_request` | error | `metric_requests` lists an id the registry doesn't have. |
| `R8.embedded_numbers` | error | See [HTML checks](#8-html-checks-r8-bindings-scripts). |
| `R8.numbers_in_text` / `R8.numbers_in_table` | warning | See [HTML checks](#8-html-checks-r8-bindings-scripts). |
| `R10.no_format` | warning | A block metric has no declared format. |
| `R10.mixed_units` | warning | A block's metrics have different units (e.g. USD and orders). |
| `html.*` | see below | HTML checks. |
| `query.failed` | error | The dry run of a block failed. |
| `spec.invalid_interaction` | error | An interaction's `from` block doesn't have the filter's dimension, or it targets a time filter. |
| `result.truncated` | warning | A block result hit the 5,000-row cap, or an agent result the 200-row cap. |
| `filter.invalid_value` | error | A filter value of the wrong shape, or a string containing `{` or `}` (the block API's 400; also checked statically on dimension-filter defaults). |
| `filter.not_listed` | error | The block API received a filter the block doesn't list (400). |

**R1 and R5 are enforced by structure, not by a check.** R1: the engine computes nothing except "Others" for additive metrics and comparison deltas, and the runtime has no aggregation helpers. R5: `filters` is required on every block, so scope is always explicit.

### 8. HTML checks (R8, bindings, scripts)

Run when `html` is given (always at publish). The HTML is parsed with the standard library's `html.parser`.

- `html.too_large` (error): over 2 MB.
- **`R8.embedded_numbers` (error):** inline `<script>` content (including `type="application/json"`) holding a literal dataset. Starting heuristic: a single array or object literal (nested ones included) with **12 or more numeric literals**, where numeric literals are at least 40% of its value tokens; or an array of **6 or more records** holding 6 or more numbers at that share (an array of 10 `{month, revenue}` objects holds only 10 numbers). Object keys, comments and strings don't count. The heuristic may be tuned, as long as the fixtures in `packages/prumi-core/tests/fixtures/r8/` keep their expected result:
  - flagged: an array of 12 numbers; an array of 10 objects like `{month: "Jan", revenue: 1234.5}`; a JSON script with chart data;
  - not flagged: a typical chart config (margins, sizes, colors, `[0, 100]` domains); a page with no inline script.
- `R8.numbers_in_text` (warning): visible text with 5 or more numbers that look like data (`1,234`, `12.5%`, `$3.4M`) in one element. `R8.numbers_in_table` (warning): a `<table>` with 5 or more numeric `<td>` cells.
- `html.binding_selector` (error): a binding key that isn't a `#id` selector, or no element has that id.
- `html.unknown_block` (error): a `prumi.on("x"` / `prumi.click("x"` string literal names a block that doesn't exist.
- `html.unbound_block` (warning): a block that's neither bound nor named in a `prumi.on(` call.
- `html.script_host` (error): an external `<script src>`, stylesheet or font URL whose host isn't in the allowed lists (section 2). The frame's CSP would block it anyway; the error says so up front.
- `html.unpinned_script` (warning): an external script from an allowed CDN without an exact version (`@1.2.3` or `/1.2.3/` in the path), or any external script without an `integrity` attribute **and** without an exact version. An unpinned library can change what's rendered without changing the certification fingerprint.

R8 can't be fully enforced (an agent can still write numbers into SVG or text). The checks catch the snapshot pattern, and the badge says "certified" only after a human review.

### 9. Engine (`prumi.engine`)

Executes **blocks**, never free queries from the browser: `run_block(dashboard_version, block_id, filter_values, viewer) -> BlockResult`.

1. Resolve the block's window from its time filter's value (or the default, or the fixed `window`) and `as_of` ([Time](#5-time-prumitime)).
2. Build the `SemanticQuery` from the block plus the values of the filters the block lists. Filters the block doesn't list are ignored (R5).
3. Run the main query. Rows are capped at **5,000**; past that, `truncated: true` plus a `result.truncated` warning. Without a `sort`, rows are ordered by time, then dimensions, so output and the cut are deterministic.
4. `top_n`: the main query is ordered by the sort metric with `limit = top_n + 1`; the extra row only tells whether more groups exist. If none do, `others` is `null`.
5. `others` (R6), per metric:
   - additive: `total − sum(top N)`;
   - non-additive: a separate query with `not_in(top N values)` on the dimension (NULL rows included, per the filter semantics). If any metric is non-additive, one such query runs for all the non-additive ones.
6. `total`: always a separate query without the block's dimensions and `time_grain` (same window and filters). Never a sum of rows.
7. `compare`: a second query for the previous window; matching and deltas as in [Time](#5-time-prumitime). `total` and `others` get no previous values in this milestone.
8. Freshness for each metric in the block.

**Block result (JSON):**

```json
{
  "block": "revenue_by_region",
  "columns": [
    {"name": "customer__region", "kind": "dimension", "label": "Region"},
    {"name": "revenue", "kind": "metric", "label": "Revenue",
     "format": {"type": "currency", "unit": "USD", "decimals": 2}, "additive": true}
  ],
  "rows": [{"customer__region": "North", "revenue": 1234.5}],
  "others": {"revenue": 210.0},
  "total": {"revenue": 4321.0},
  "window": {"start": "2026-07-20", "end": "2026-10-11", "grain": "week", "partial": false},
  "comparison_window": null,
  "freshness": {"revenue": "2026-10-14"},
  "warnings": [],
  "truncated": false
}
```

- Rows are objects keyed by column name. Time blocks have a `metric_time` column (ISO date of the period start) and a `_partial: true` key on rows of a partial period. Blocks with `compare` add `_previous`, `_delta` and `_delta_pct` objects (keyed by metric) to each row.
- Names starting with `_` are reserved for Prumi.

**Agent output (`prumi.agent_output.for_agent(result, policy)`).** Every result returned to an agent passes through this one function. The only policy in this milestone caps rows at **200**: past that, the result has `truncated: true`, `row_count` (up to the 5,000 cap, or "more than 5000"), and `total` from a separate source query even if it wasn't requested.

### 10. Registry (`prumi.registry`)

SQLite (standard library) at `<data_dir>/registry.sqlite3`, with a `schema_version` table and plain-SQL migrations run at startup.

| Table | Columns |
|---|---|
| `dashboards` | `id` (PK), `title`, `owner`, `created_at`, `current_version` |
| `versions` | `dashboard_id`, `version` (from 1), `spec_json` (canonical), `html`, `spec_hash`, `html_hash`, `fingerprint`, `created_at`; PK (`dashboard_id`, `version`) |
| `certifications` | `id`, `dashboard_id`, `version`, `fingerprint`, `certified_by`, `certified_at`, `revoked_at`, `revoked_reason` |
| `metric_requests` | `id`, `description`, `reason`, `dashboard_id` (nullable), `requested_by`, `created_at` |

- Versions are immutable. HTML is stored with line endings normalized to `\n`, and never contains query results.
- **Publish:** validate (errors block it). A new id creates version 1. An existing id creates version N+1, unless the fingerprint equals the current version's, in which case nothing is written and the current version is returned. `owner` is `"local"`.
- **Fingerprint:** `spec_hash` = sha256 of the canonical spec JSON (pydantic dump with defaults, keys sorted, no whitespace, UTF-8). `html_hash` = sha256 of the stored HTML. `fingerprint = "v0:" + sha256(spec_hash + ":" + html_hash)`. A golden test pins the output for a fixture.
- **Status** is computed, never stored: a version is **certified** if a non-revoked certification exists for that dashboard with the same fingerprint; otherwise it's **draft**. So:
  - publishing any change makes the current version a draft (automatic revocation);
  - republishing content identical to a certified version makes it certified again, because it's what was reviewed.
- **Delete** removes the dashboard, its versions and its certifications. Metric requests stay, with `dashboard_id` set to NULL.
- The CLI and the server can use the registry at the same time (SQLite WAL mode). The server reads status on every request, so a `certify` shows up on the next page load.

### 11. CLI (`prumi`, argparse)

| Command | Does |
|---|---|
| `prumi serve [--config]` | Starts the server. |
| `prumi list` | Dashboards: id, title, current version, status. |
| `prumi certify <id> <version> [--by NAME]` | Re-validates that version against the current catalog (static checks + dry run). Refuses if there are errors. Otherwise records a certification (`--by` defaults to the OS user name) and prints the fingerprint. |
| `prumi uncertify <id> [--reason TEXT]` | Revokes the dashboard's active certifications. |
| `prumi delete <id> [--yes]` | Deletes the dashboard after a confirmation prompt (skipped with `--yes`). |
| `prumi requests` | Lists metric requests. |
| `prumi validate <spec.yaml|json> [--html FILE]` | Prints the validation report; exits 1 on errors. |

Commands that need the catalog (`certify`, `validate`) load the adapter from the same config. Exit code 0 on success, 1 on a refused action, 2 on usage errors.

### 12. HTTP server and dashboard hosting (`prumi.server`)

One Starlette app served by uvicorn: the MCP app (section 14) is mounted at `/mcp`.

**Request guards, on every route:**
- **Host check:** reject (403) any request whose `Host` header isn't the configured `host:port` (or `localhost:port`). This blocks DNS rebinding, where a malicious web page makes the browser talk to a local unauthenticated server.
- No CORS headers anywhere. `POST /api/...` requires `Content-Type: application/json`, so a cross-site page can't send it without a preflight that fails.

**Routes:**

| Route | Returns |
|---|---|
| `GET /` | A plain HTML list of dashboards with status (for the demo). |
| `GET /runtime.js` | The runtime (runs in the frame). |
| `GET /shell.js` | The shell's script (filter state, fetching, badge). |
| `GET /d/<id>` | The shell for the current version. |
| `GET /d/<id>/v/<n>` | The shell for version n (for reviewing before certifying). |
| `GET /d/<id>/v/<n>/frame` | The stored HTML with the runtime injected. Sandboxed (below). |
| `GET /api/dashboards/<id>/versions/<n>/status` | `{status, version, current_version, certified_by, certified_at, fingerprint}` |
| `POST /api/dashboards/<id>/versions/<n>/blocks/<block>` | Body `{"filters": {"<filter id>": <value>}}` → block result. |
| `GET /api/dashboards/<id>/versions/<n>/filters/<filter>/values` | Distinct values of a dimension filter. |

- **The browser only sends filter values.** The server loads the block from the stored version and builds the query itself. Unknown dashboard, version, block or filter (or a time filter on the values route): 404. A filter value of the wrong shape or a string with `{`/`}` (`filter.invalid_value`), or a filter the block doesn't list (`filter.not_listed`): 400 with an issue list. A POST without `Content-Type: application/json`: 415. The source failing: 502 with a `query.failed` issue (the log records only the error type).

**The shell** (`/d/<id>`) is Prumi's own page: a bar with the dashboard title, the **badge** (`Draft · not certified`, `Certified by <name> on <date>`, or `Version n of m · not current`), freshness (`Data through <earliest data_through among the dashboard's metrics>`, taken from the block results), and a details toggle listing block warnings (partial periods, snapped windows, stale data). Below the bar, the frame fills the page:

```html
<iframe src="/d/<id>/v/<n>/frame" sandbox="allow-scripts"></iframe>
```

**The frame** response has these headers:
- `Content-Security-Policy: sandbox allow-scripts; default-src 'none'; script-src <server origin> 'unsafe-inline' <script_hosts>; style-src 'unsafe-inline' <style_hosts>; font-src data: <font_hosts>; img-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors <server origin>`
- The `sandbox` directive applies even if someone opens the frame URL directly.
- The server's origin is listed explicitly (not `'self'`), because the sandboxed document has an opaque origin. It's taken from the request's `Host` header, which the Host check has already validated, so both `127.0.0.1:8484` and `localhost:8484` work. Browser tests must confirm `runtime.js` loads.
- `connect-src 'none'`: the dashboard can't call any server, Prumi included. Data arrives only through the shell. Images are limited to `data:` and `blob:`, so `<img src="https://elsewhere/?data">` can't send data out either.

The server injects `<script src="<server origin>/runtime.js"></script>` as the first child of `<head>` (creating `<head>` if missing). If the HTML loads `/runtime.js` itself, the runtime ignores the second load.

### 13. Runtime (`prumi-runtime/src/runtime.js`)

Plain JS, no dependencies, an IIFE that defines `window.prumi`. It runs inside the frame and talks to the shell by `postMessage`. The shell part (filter state, fetching, badge) is a second small script, `shell.js`; both live in `packages/prumi-runtime/src/`.

**API for dashboard HTML:**

```js
prumi.on(blockId, (result) => { /* draw */ })   // called on load and after each filter change
prumi.onError(blockId, (issue) => { ... })
prumi.setFilter(filterId, value)                // [values] | [] | window object
prumi.getFilter(filterId)
prumi.filterValues(filterId)                    // Promise<values[]>
prumi.onFilters((filters) => { ... })           // current values of all filters
prumi.click(blockId, row)                       // applies the interactions declared for that block
prumi.format(value, metricName)                 // R10 formatting
prumi.label(name)                               // a metric's or dimension's catalog label
```

- `click`: for each interaction `from: blockId`, it sets `sets_filter` to `[row[<filter's dimension>]]`. Clicking the same value again clears the filter. The validator checks that the block has that dimension.
- `format`: `Intl.NumberFormat` with the browser's locale. `number`: up to `decimals ?? 2` fraction digits. `integer`: 0. `currency`: `style: "currency"` with `unit`. `percent`: `style: "percent"` with at most `decimals ?? 1` fraction digits (0.25 → "25%"). `currency` with `decimals` fixes both the minimum and maximum digits. `null` → `"–"`.
- **No aggregation helpers.** The runtime never sums, averages or re-groups rows (R1). Cross-filtering re-queries the server; it never filters rows in the browser.
- Outside a Prumi shell (no parent, or the parent doesn't answer the handshake within 2 s), the runtime shows a fixed notice "This dashboard only works inside Prumi" and calls no callbacks. A copied HTML file has no data and no badge.

**Messages** (every message carries `prumi: 0` as the protocol version). The shell only accepts messages whose `event.source` is the frame's `contentWindow`, and the runtime only those from `window.parent`. With an opaque origin, checking `event.origin` isn't possible.

| Direction | `type` | Payload |
|---|---|---|
| frame → shell | `ready` | `blocks`: ids the HTML subscribed to |
| shell → frame | `init` | filter definitions and current values, catalog labels and formats for the dashboard's metrics and dimensions |
| shell → frame | `data` / `error` | `block`, `result` or `issue` |
| frame → shell | `setFilter` | `id`, `value` |
| frame → shell | `click` | `block`, `row` |
| frame → shell | `filterValues` | `id`, `request` (correlation id) |
| shell → frame | `filterValues` | `request`, `values` |
| shell → frame | `filters` | current values of all filters |

The shell holds the filter state, re-fetches only the blocks that list a changed filter, and pushes the results.

### 14. MCP server (`prumi.mcp`)

Built with the official Python SDK (`MCPServer`, called `FastMCP` before `mcp` 2.x), Streamable HTTP transport, stateless mode, mounted at `/mcp`. The SDK's DNS-rebinding check stays on, configured with Prumi's allowed hosts. Server name `prumi`. Tool inputs and outputs are pydantic models, so the SDK publishes their JSON schemas to the agent.

| Tool | Input | Output |
|---|---|---|
| `find_metrics` | `question: str`, `limit: int = 20` | Up to `limit` metrics: `name`, `label`, `description`, `kind`, `additive`, `format`, `owner`, `score`, ranked by score. |
| `describe_metric` | `name: str` | Everything in the catalog for the metric, its valid dimensions (name, label, description), time grains, the metric's own filter, and `caveats` (generated: "non-additive: never sum or average its rows", "no format declared"). Unknown name: error with suggestions. |
| `query` | A block-shaped query: `metrics`, `dimensions`, `time_grain`, `window` (**required**), `where: [{dimension, op, values}]`, `sort`, `top_n`, `others`, `total`, `compare` | The block result through `for_agent` (≤ 200 rows), or validation issues. |
| `validate_spec` | `spec`, `html: str \| None` | `{ok, errors, warnings}` |
| `publish_dashboard` | `spec`, `html` | `{id, version, url, status, warnings}`; `status` is computed (`draft`, or `certified` when the content is identical to a certified version), or the errors that blocked publishing. `url` is `http://<host>:<port>/d/<id>`. |
| `request_metric` | `description: str`, `reason: str`, `dashboard_id: str \| None` | `{id, message}`. The id can go in the spec's `metric_requests`. |

- **`find_metrics` ranking:** lowercase, split on non-alphanumerics and `_`, drop a short English stopword list. Score = 2 × matches in name/label + 1 × matches in description. Ties broken by name. No synonyms and no embeddings. A catalog smaller than `limit` is returned whole.
- `query` uses the same validation rules as a block (R2–R7, R10), and `where` uses the adapter filter ops.
- Errors are returned as tool error results whose only content is the issue list (not raised, which would add the SDK's own error prefix).

**Server instructions** (sent to the agent at initialization):

> Prumi serves official metrics from the company's semantic layer. Build dashboards with it like this:
> 1. Call `find_metrics` with the user's question, then `describe_metric` for each metric you plan to use. Use only these metrics. If the right one doesn't exist, call `request_metric` and tell the user; never compute or approximate a metric yourself.
> 2. Use `query` to look at the data while designing. Results are capped; they're for exploring, not for copying into the page.
> 3. Write a spec and the HTML. Every number on the page must come from `prumi.on(...)` in the HTML. Never write numbers, totals or datasets into the HTML, and never compute totals, averages or ratios in JavaScript; declare `total`, `others` or `compare` in the spec instead. Format values with `prumi.format`.
> 4. Every data block must list its `filters` explicitly. Prefer full-period windows like `{last: 12, grain: week}`.
> 5. Call `validate_spec` with the spec and the HTML and fix every error. Then `publish_dashboard` and give the user the URL. The dashboard starts as an uncertified draft; only the data team can certify it.

### 15. Demo dbt project (`examples/demo-dbt-project`)

A stand-in "customer" project: not product code, outside the uv workspace, with its own `pyproject.toml` (`dbt-core==1.12.6`, `dbt-duckdb==1.11.0`, `duckdb`).

**Data:** a seeded, deterministic synthetic online shop. `generate.py` writes raw tables into the `raw` schema of `demo.duckdb` (no CSV seeds), and dbt models build on them.

| Raw table | Columns | Notes |
|---|---|---|
| `customers` | `customer_id`, `signup_date`, `country`, `region`, `segment` | About 3,000 customers; 2% have NULL `region` |
| `products` | `product_id`, `name`, `category`, `unit_cost` | About 60 products in 6 categories |
| `orders` | `order_id`, `order_date`, `customer_id`, `channel` (`web`, `store`, `marketplace`) | 2024-01-01 to 2026-10-14; growth plus weekly and yearly seasonality; fewer orders on Sundays |
| `order_items` | `order_item_id`, `order_id`, `product_id`, `quantity`, `unit_price`, `discount` | |
| `inventory_snapshots` | `snapshot_date`, `product_id`, `units_on_hand` | Daily |

**Semantic models and metrics** (legacy `measures:` YAML, the form the spike tested):

| Metric | Kind | Format | Additive | Trap it plants |
|---|---|---|---|---|
| `revenue` | simple, sum | currency USD | yes | |
| `orders` | simple, count | integer | yes | |
| `units_sold` | simple, sum | integer, `units` | yes | |
| `average_order_value` | ratio revenue / orders | currency USD | no | Average of averages |
| `customers` | simple, count_distinct | integer | no | Summed distinct counts |
| `gross_margin` | derived revenue − cost | currency USD | no | Conservative additivity |
| `gross_margin_pct` | ratio gross_margin / revenue | percent | no | Ratio at grain |
| `units_on_hand` | simple, sum with `non_additive_dimension` on `snapshot_date` (last value) | integer | no | Semi-additive balance |
| `discount_rate` | ratio discounts / gross_sales | **none declared** | no | R10 warning |
| `cost` | simple, sum | currency USD | yes | Input of `gross_margin` |
| `gross_sales` | simple, sum | currency USD | yes | Input of `discount_rate` |
| `discounts` | simple, sum | currency USD | yes | Input of `discount_rate` |

The last three exist because MetricFlow builds ratio and derived metrics only from other metrics, and this YAML form can't hide a metric. The catalog shows all twelve: whether a metric is public is the source's decision.

- Dimensions: `customer__region`, `customer__country`, `customer__segment`, `order__channel`, `product__category`. The fact models carry the foreign keys they need (e.g. `customer_id` on order items), so every dimension is one entity hop away and has these names. `units_on_hand` isn't joinable to customer dimensions (R2 test case).
- `as_of = 2026-10-15` in the demo config, so October 2026 is a partial month and data ends the day before.
- **Build:** `uv run --project examples/demo-dbt-project python examples/demo-dbt-project/build.py` generates the data and runs `dbt build` then `dbt parse` in a subprocess (spike caveat 8). It's idempotent and finishes in under a minute. Outputs (`demo.duckdb`, `target/`) are git-ignored.
- A pytest session fixture builds the demo once when the outputs are missing or older than the generator and models.
- `README.md` explains the planted traps and `examples/demo-dbt-project/prumi.toml` is the config the demo runs with.

### 16. Logging

Standard `logging`. Each executed query logs: origin (`block <dashboard>/<v>/<block>`, `agent query`, `dry run <dashboard>`, `filter values <dashboard>/<v>/<filter>`), step, viewer id, duration, row count. **Never row values or filter values**, at any level. Compiled SQL is logged only at DEBUG and with every literal replaced by `?`, because the SQL inlines filter values and window dates. MetricFlow's own loggers are kept at WARNING, since its DEBUG output includes filter values.

### 17. Docs

README: quick start (build the demo, `prumi serve`, connect an agent), "Connect your agent" (Claude Code: `claude mcp add --transport http prumi http://127.0.0.1:8484/mcp`; clients that connect from their vendor's cloud can't reach a local server), and "Where your data goes" (the data flow above).

The dbt adapter's README has "Preparing your dbt project": what Prumi reads, and the recommended `config.meta.prumi` format convention with the reason for it. The main README's quick start links to it.

The main README links to [`docs/bi-rules.md`](../../docs/bi-rules.md). When an issue code changes, that file changes with it.

### 18. First-use fixes (added 2026-10-18; status: draft)

The first real agent session (Claude Code on the demo) published a correct-looking dashboard, but took about 6 minutes, and about two thirds of that time and of all output tokens went into sending the same 26 KB HTML twice (to `validate_spec`, then to `publish_dashboard`). The agent also had to guess the page API and the spec format. It guessed `prumi.format(value, column.format)`, while the runtime expects a metric name, so every number on that page lost its currency and percent format without any error. These fixes ship in 0.1.0.

**1. `authoring_guide` tool.** No input. Returns one Markdown text, kept as `packages/prumi-core/src/prumi/mcp/authoring_guide.md`:
- **The page API:** every `prumi.*` function with its arguments and return value; the exact object `prumi.on` callbacks receive (the block result of section 9, including `_partial`, `_previous`, `_delta`, `_delta_pct`, `others`, `total`, `window`, `freshness`); when callbacks fire (on load, then after each change to a filter the block lists); how errors arrive (`prumi.onError` gets an issue); what an empty result looks like (`rows: []`).
- **A minimal valid spec and a minimal HTML page** that uses it with `prumi.on`, `prumi.format` and `prumi.click`.
- **The spec JSON Schema** (the committed file, inlined).

Tests: the guide's spec and HTML pass `validate_spec`; the guide names every function the runtime defines on `window.prumi`; the inlined schema equals the committed one.

**2. Publish by candidate.** When `validate_spec` gets both `spec` and `html` and finds no errors, its result adds `candidate_id` (the fingerprint, `v0:...`). The server keeps validated candidates in memory (up to 50, for 1 hour). `publish_dashboard` takes **either** `spec` + `html` **or** `candidate_id`. It always re-validates the stored content, since the catalog may have changed. An unknown or expired id gives `publish.unknown_candidate` (error), with the hint "call `validate_spec` again with the spec and HTML". Candidates are lost on restart; that's fine.

**3. Plain validation messages.** `spec.invalid` messages never use pydantic's words ("tuple", "model", "union", "tagged-union", "dict", "Input should be"). Each says what the field must be, in the spec's terms:
- missing field: "`dashboard.id` is required";
- unknown field: "`dashboard.description` isn't a field of `dashboard`; its fields are `id`, `title`";
- wrong container: "`data` must be a list of blocks";
- wrong value: "`grain` must be one of `day`, `week`, `month`, `quarter`, `year`";
- wrong type: "`top_n` must be a whole number".

**4. A forgiving, noisy `prumi.format`.** `prumi.format(value, metric)` also accepts a column's `format` object as the second argument. Given anything else (an unknown metric name, a missing argument), it formats as a plain number **and** logs `console.warn("prumi.format: unknown metric ...")` once per argument, so a wrong call is visible in the browser console.

**5. Local URLs are labeled.** When the server binds to a loopback address, `publish_dashboard` adds `note: "This link only works on this machine."`

**6. Server instructions.** Steps 3 and 5 of section 14 become:

> 3. Call `authoring_guide` once before writing anything: it has the spec format, the page API and a working example. Then write a spec and the HTML. Every number on the page must come from `prumi.on(...)` in the HTML. Never write numbers, totals or datasets into the HTML, and never compute totals, averages or ratios in JavaScript; declare `total`, `others` or `compare` in the spec instead. Format values with `prumi.format`.
> 5. Call `validate_spec` with the spec and the HTML and fix every error. Then call `publish_dashboard` with the `candidate_id` from the last successful validation (don't resend the spec and HTML), and give the user the URL. The dashboard starts as an uncertified draft; only the data team can certify it.

## Out of scope

| Not in this milestone | Where it goes |
|---|---|
| Login (OIDC), viewers other than `local`, identity passthrough, identity-mode acknowledgment, R9 | Installable |
| Binding to non-loopback addresses | Installable (with login) |
| Shared status, Curator role, `request_certification`, sharing | Installable |
| Personal sources (uploads), `upload_source`, mixing sources | Installable |
| `find_sources`, `check_access`, handoff (`get_handoff`) | Installable |
| Warehouses other than DuckDB; declared supported dbt versions | Installable |
| Fiscal calendars (MetricFlow custom granularities) | Installable |
| `docker compose` packaging and a trial mode | Installable |
| Bootstrap of draft semantic models | Installable |
| Power BI adapter; catalog search for large catalogs | Power BI |
| Time dimensions other than `metric_time`; grains below a day | Later |
| Comparison series on time-grain blocks | Later |
| Filters on metric values (HAVING) | Later |
| A shared filter that targets an attribute (e.g. customer → region) and uses each block's own join path | Later |
| A results cache | Later, when needed |
| Reloading the manifest without a restart | Later |
| Accepting presentation-only changes without re-certification; a review UI | Later |
| Agent-data modes other than the row cap (masked values, shape only) | Later |
| The newer dbt metrics YAML (metrics on models), untested by the spike | Later |
| A `prumi` meta-package on PyPI | Open (ADR 0001) |

## Design for later

- **`execute(query, viewer)` takes a viewer** even though there's only `LOCAL_VIEWER`, and so does every engine function that runs a query (block execution, freshness, filter values). Later milestones run queries as each viewer.
- **Identity mode is declared now** (`capabilities().identity_mode`). The DuckDB connection is a shared account; Installable adds the admin acknowledgment.
- **Connections open per `execute` call**, so a later per-viewer connection replaces only the connection factory.
- **No results cache.** If one is added, its key must include the source, the compiled query and `viewer.security_context`.
- **The sandboxed frame** keeps dashboard code away from the shell, the badge and (later) the viewer's session. Login can be added without changing the runtime API.
- **Sources are a mapping keyed by name**, with one entry. Metric names are unqualified while there is one source; catalog entries already carry their source name.
- **`spec_version: 0`** and the committed JSON Schema: the spec format may change before 1.0, with migration notes.
- **`calendar: standard`** is accepted on time filters, so fiscal calendars only add values.
- **Spec and HTML hashes are stored separately**, so a later review flow can tell a presentation-only change from a semantic one.
- **`for_agent(result, policy)`** is the only path from results to agents, so agent-data modes can be added as policies.
- **The postMessage protocol is versioned** (`prumi: 0`).

## Acceptance criteria

Each criterion has at least one automated test unless it says otherwise.

**Workspace and adapter**
1. On a clean clone, `uv sync`, the demo build, `uv run pytest` and `uv run ruff check` all pass.
2. Only `prumi_adapter_dbt/_metricflow.py` imports `metricflow` or `metricflow_semantics`; `prumi` imports no adapter package (tests check both).
3. **Contract test:** the spike's checks (ratio and distinct count by month, by region and in total; filter plus window; day grain) pass on the demo project against the pinned MetricFlow, and the compile-only client records zero calls. The test also covers what this spec relies on beyond the spike: a window with no time group-by, a dimension-values query, `order_by` + `limit`, `not_in` and NULL handling, the `non_additive_dimension` metric, and week grain starting on Monday.
4. Startup fails with a message naming the problem for: a missing manifest, a manifest MetricFlow can't parse, an unsupported connection type, and a relation that doesn't resolve (e.g. a renamed DuckDB file).
5. The demo catalog matches the table in section 15: kinds, additivity, formats, owner, and an `R10` catalog warning for `discount_rate`.
6. Filter values containing `{` or `}` are rejected; values containing `'` are escaped and return correct rows; NULL semantics for `eq`/`in`/`ne`/`not_in` match section 3.

**Time**
7. Window resolution is covered by a table-driven test with a fixed `as_of`: every window form, ISO weeks, snapping with `R3.window_snapped`, `R3.window_too_short`, `R3.empty_window`, the current partial period flag, and every row of the grain compatibility rule.
8. Comparison windows match section 5 for `previous_period` and `previous_year` at every grain, including `to_date` clamping (March → February) and Feb 29.
9. A demo block over a window ending after the data's last day gets `R3.stale_data`; a time block with `include_current` marks the October 2026 row `_partial`.

**BI rules**
10. **R1:** for `average_order_value`, `customers`, `gross_margin_pct` and `units_on_hand`, block results by month, by region and in total equal hand-written SQL over the demo warehouse.
11. **R6:** `total` equals a source query without the dimension; for additive metrics `others` equals `total − sum(top N)` and equals a `not_in` source query; for non-additive metrics `others` equals the `not_in` source query, and NULL-region rows are counted in Others.
12. Each validator error and warning code in section 7 has a fixture spec that produces it and only it. Issues carry `code`, `severity`, `path`, `message` and `hint`.
13. **R2:** `units_on_hand` by `customer__region` → `R2.invalid_dimension`; a region filter on that block → `R2.filter_dimension_invalid`.
14. **R7:** `revnue` → `R7.unknown_metric` suggesting `revenue`, with a hint naming `request_metric`.
15. **R8:** the fixtures in `tests/fixtures/r8/` produce exactly their expected issues.
16. The committed JSON Schema matches the pydantic models.

**Registry and certification**
17. Publishing a new id creates version 1; changing the spec or the HTML creates version N+1; republishing identical content creates nothing. The fingerprint golden test passes.
18. `certify` on a valid version makes it certified; publishing a change makes the current version a draft; republishing content identical to the certified version makes it certified again; `uncertify` revokes; `delete` removes the dashboard, versions and certifications and keeps the metric requests.
19. `certify` refuses a version whose spec now has validation errors (e.g. a metric removed from the manifest) and exits 1.

**Server, hosting and runtime**
20. The server refuses to start with a non-loopback `host`. Requests with another `Host` header get 403.
21. The block API only accepts filter values: unknown ids → 404; a filter the block doesn't list, a wrongly shaped value, or a value with `{`/`}` → 400.
22. *(browser test)* In the shell, the badge shows draft/certified from the server, and freshness shows the demo's last data day. Script in the frame can't read or change the shell's DOM, can't `fetch` anywhere, and can't load an image from another host.
23. *(browser test)* The runtime: `prumi.on` delivers block results on load; `setFilter` re-fetches only the blocks that list the filter; `click` applies the declared interaction and a second click clears it; `filterValues` returns the dimension's values; `format` renders each format type.
24. *(browser test)* The frame HTML opened as a standalone file shows the "only works inside Prumi" notice and no data.
25. Logs from a full test run contain no row values or filter values (a test runs blocks and agent queries with a sentinel filter value and asserts it never appears in captured logs).

**MCP**
26. An MCP client over Streamable HTTP lists exactly the six tools, and the server instructions match section 14.
27. `find_metrics("average order value")` ranks `average_order_value` first; `describe_metric("customers")` includes the non-additive caveat.
28. `query` for revenue by day over two years returns 200 rows, `truncated: true`, `row_count`, and a `total` equal to a source query.
29. **End to end:** a scripted MCP session runs `find_metrics` → `query` → `validate_spec` (with an error, then fixed) → `publish_dashboard`; a browser opens the URL and the bound numbers equal hand-written SQL; `prumi certify` makes the badge certified; publishing an HTML edit makes it a draft again.

**Docs and release**
30. The README has the quick start (linking to the dbt adapter's "Preparing your dbt project"), "Connect your agent" and "Where your data goes" sections. *(review)*
31. `prumi-core`, `prumi-adapter-dbt` and the runtime are versioned `0.1.0`; `uv build` produces wheels, and the `prumi-core` wheel contains `runtime.js` and `shell.js`.

**First-use fixes (section 18)**
32. `authoring_guide` exists; its example spec and HTML validate, it names every `window.prumi` function, and its schema equals the committed one.
33. `validate_spec` with spec + HTML and no errors returns a `candidate_id`; `publish_dashboard(candidate_id=...)` publishes the same version as sending the content; an unknown or expired id gives `publish.unknown_candidate`; a catalog change between validate and publish is caught by re-validation.
34. Over the validator's `spec.invalid` fixtures, no message contains "tuple", "model", "union", "dict" or "Input should be", and each of the five message kinds in section 18 has a fixture.
35. *(browser test)* `prumi.format(1234.5, "revenue")` and `prumi.format(1234.5, <revenue's format object>)` give the same currency string; `prumi.format(1, "nope")` gives a plain number and one console warning.
36. On a loopback server, `publish_dashboard` returns the local-only note. The MCP instructions match section 18.
37. *(review)* A fresh agent session given the README's example prompt calls `authoring_guide`, needs no rounds to discover the spec shape, sends the HTML once (to `validate_spec`) and publishes by `candidate_id`.
