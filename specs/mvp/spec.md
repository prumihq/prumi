# MVP: prove the core loop on one machine

Milestone: MVP · Release: 0.1.0 (planned) · Status: accepted (sections 1–17 on 2026-10-17, sections 18–20 on 2026-10-18); section 21 draft

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
- **`query` results:** a profile of the result (counts, empties, label lengths, magnitudes), never values or names (section 19.16). No company data reaches the agent or its LLM provider.
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
locale = "en-US"            # optional: how every page shows numbers (section 21)
script_hosts = ["cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com"]
style_hosts = ["fonts.googleapis.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com"]
font_hosts = ["fonts.gstatic.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com"]

[source]
adapter = "dbt"
project = "prumi_demo"       # optional: shown in the home page header

[source.dbt]
semantic_manifest = "examples/demo-dbt-project/target/semantic_manifest.json"

[source.dbt.connection]
type = "duckdb"
path = "examples/demo-dbt-project/demo.duckdb"
```

- Relative paths resolve against the config file's folder.
- **The server refuses to start if `host` isn't a loopback address** (`127.0.0.1`, `::1`, `localhost`). There's no login in this milestone, so binding elsewhere would expose the warehouse to the network.
- `project` is an optional display name for the source, shown in the home page header (section 20). Core reads it; adapters never see it. Without it, the header leaves the name out.
- `locale` is a BCP 47 tag (`en-US`, `pt-BR`, `de-DE`); default `en-US`. One locale for the whole server: every viewer sees numbers formatted the same way, whatever their browser's language (section 21). An unknown tag stops `prumi serve` at startup.
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
| `GET /` | The home page (section 20). |
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
- `format`: `Intl.NumberFormat` with the server's `locale` (section 21), not the browser's. `number`: up to `decimals ?? 2` fraction digits. `integer`: 0. `currency`: `style: "currency"` with `unit`. `percent`: `style: "percent"` with at most `decimals ?? 1` fraction digits (0.25 → "25%"). `currency` with `decimals` fixes both the minimum and maximum digits. `null` → `"–"`.
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
| `query` | A block-shaped query: `metrics`, `dimensions`, `time_grain`, `window` (**required**), `where: [{dimension, op, values}]`, `sort`, `top_n`, `others`, `total`, `compare` | A profile of the block result through `for_agent` (section 19.16), or validation issues. |
| `validate_spec` | `spec`, `html: str \| None` | `{ok, errors, warnings}` |
| `publish_dashboard` | `spec` + `html`, or `candidate_id` (section 18) | `{id, version, url, status, warnings}`; `status` is computed (`draft`, or `certified` when the content is identical to a certified version), or the errors that blocked publishing. `url` is `http://<host>:<port>/d/<id>`. |
| `request_metric` | `description: str`, `reason: str`, `dashboard_id: str \| None` | `{id, message}`. The id can go in the spec's `metric_requests`. |
| `authoring_guide` | none | The authoring guide (section 18). |

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

README: quick start (build the demo, `prumi serve`, connect an agent), "Connect your agent" (Claude Code: `claude mcp add --scope user --transport http prumi http://127.0.0.1:8484/mcp`; clients that connect from their vendor's cloud can't reach a local server), and "Where your data goes" (the data flow above).

The dbt adapter's README has "Preparing your dbt project": what Prumi reads, and the recommended `config.meta.prumi` format convention with the reason for it. The main README's quick start links to it.

The main README links to [`docs/bi-rules.md`](../../docs/bi-rules.md). When an issue code changes, that file changes with it.

### 18. First-use fixes (added and accepted 2026-10-18)

The first real agent session (Claude Code on the demo) published a correct-looking dashboard, but took about 6 minutes, and about two thirds of that time and of all output tokens went into sending the same 26 KB HTML twice (to `validate_spec`, then to `publish_dashboard`). The agent also had to guess the page API and the spec format. It guessed `prumi.format(value, column.format)`, while the runtime expects a metric name, so every number on that page lost its currency and percent format without any error. These fixes ship in 0.1.0.

**1. `authoring_guide` tool.** No input. Returns one Markdown text, kept as `packages/prumi-core/src/prumi/mcp/authoring_guide.md`:
- **The page API:** every `prumi.*` function with its arguments and return value; the exact object `prumi.on` callbacks receive (the block result of section 9, including `_partial`, `_previous`, `_delta`, `_delta_pct`, `others`, `total`, `window`, `freshness`); when callbacks fire (on load, then after each change to a filter the block lists); how errors arrive (`prumi.onError` gets an issue); what an empty result looks like (`rows: []`).
- **A minimal valid spec and a minimal HTML page** that uses it with `prumi.on`, `prumi.format` and `prumi.click`.
- **The spec JSON Schema** (the committed file, inlined).

Tests: the guide's spec and HTML pass `validate_spec`; the guide names every function the runtime defines on `window.prumi`; the inlined schema equals the committed one.

**2. Publish by candidate.** When `validate_spec` gets both `spec` and `html` and finds no errors, its result adds `candidate_id` (the fingerprint, `v0:...`). The server keeps validated candidates in memory (up to 50, for 1 hour). `publish_dashboard` takes **either** `spec` + `html` **or** `candidate_id`. It always re-validates the stored content, since the catalog may have changed. An unknown or expired id gives `publish.unknown_candidate` (error), with the hint "call `validate_spec` again with the spec and HTML". A call with neither `candidate_id` nor `spec` + `html`, or with both, gives `publish.invalid_arguments` (error). Candidates are lost on restart; that's fine.

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

### 19. Trusted numbers, editing and the second-run fixes (added and accepted 2026-10-18)

The second agent run (2026-10-18) published without guessing, but two things stood out. First, the "numbers only come from Prumi" rule was only partly enforced: a page with a hard-coded `$2,742,409`, a JavaScript-computed average and a `fetch()` call passed validation. Second, a user can't continue work on a dashboard in a later session. This section closes both, plus the smaller gaps both runs found. It changes section 18 item 4: `prumi.format` no longer accepts a format object.

#### 19.1 Number provenance

**Every number shown on a dashboard must be a value Prumi delivered.** It doesn't matter how a wrong number got there (hard-coded, computed in JavaScript, copied from an old result): if Prumi didn't deliver it, it's flagged.

- **Delivered values:** every metric value in the block results for the current filter state (rows, `total`, `others`, `_previous`, `_delta`, `_delta_pct`).
- **Allowed non-data numbers:**
  - dates and years in common formats;
  - numbers taken from the spec or from result metadata (window length, `top_n`, row counts);
  - tick-like values (at most 2 significant digits, e.g. `0`, `50K`, `2.5M`, `20%`), but only inside elements marked `data-prumi-role="axis"`.
- **What's read:** visible text, i.e. text nodes outside `<script>`/`<style>`, SVG `<text>`/`<tspan>`, and text that appears later, such as tooltips.
- **Matching:** a number on screen matches a delivered value when that value, rounded to the precision shown and scaled for `%` or `K`/`M`/`B`, gives the same number. Currency symbols, separators and signs are ignored. `$2.7M` matches `2742409.17`; `$228,534` doesn't match anything if only `2742409.17` was delivered.
- **Canvas is not allowed.** Text drawn into a `<canvas>` can't be read, so any `<canvas>` element is an error (`html.canvas`). SVG and HTML charts are fine.
- The heuristics may be tuned, as long as the fixtures in `packages/prumi-core/tests/fixtures/provenance/` keep their expected result:
  - **Must fail:** the probe page from the second review (a hard-coded `$2,742,409`, a `var DATA = [...]` dataset, a `revenue / 12` shown as text); a page that shows a total summed in JavaScript; a page that shows an average of averages.
  - **Must pass:** the first-run dashboard (`~/test/weekly-revenue-dashboard.html` with its spec, copied into the fixtures); the second-run dashboard (`~/test2/dashboard.html` and `spec.json`); the `authoring_guide` example; a chart with marked axis ticks; text with dates and "Last 12 weeks".

The check runs in two places.

**a. On the server, at validation: authoritative.** When `validate_spec` gets HTML and the static checks and dry run pass, Prumi loads the page in headless Chromium (Playwright). The frame is fed the dry-run results, so there are no extra queries. Prumi waits until rendering settles (no DOM changes for 300 ms, at most 10 s), then reports:

| Code | Severity | When |
|---|---|---|
| `R8.unverified_number` | error | A shown number isn't a delivered value. The issue gives the text and the element's path; at most 20 are listed |
| `html.canvas` | error | The page uses `<canvas>` |
| `html.runtime_error` | error | The page threw an error, or a `prumi.on` callback did |
| `html.render_timeout` | error | Rendering didn't settle within 10 s |
| `html.blocked_request` | warning | The page tried a network call, which the frame's CSP blocks |

- **The scan runs outside the page's JavaScript** (Playwright's isolated world), against the values the server itself computed. Page code can't switch it off or feed it fake values.
- **Errors block publishing**, because `publish_dashboard` re-validates.
- One Chromium is launched per server process and reused, with a new browser context per check. Checks run one at a time. Target: under 3 s for the demo dashboards.
- Only the filters' default values are checked here.

**b. In the browser, whenever the page is viewed: catches other filter states.** The runtime runs the same matching after each render settles (debounced 300 ms) and reports `{filters, flagged, canvas, errors}` to the shell. The shell posts it to `POST /api/dashboards/<id>/versions/<n>/check`, and the registry keeps the latest report per version and filter state. **Nothing is shown to viewers:** pages with flagged numbers can't be published (a), so a report here means a filter state the server didn't check. Reports appear in `get_dashboard` and block certification. Page code could interfere with this in-page check; it's informational. The server check (a) and certification (19.2) are the guarantees.

#### 19.2 Certification requires clean numbers

`prumi certify <id> <version>` refuses (exit 1) if:
- the server check (19.1a), re-run at certification time, reports errors; or
- any stored browser report for that version has flagged numbers, canvas or runtime errors. The message lists the filter state of each report.

What a reviewer still has to judge is only **visual encoding**, e.g. whether a bar's height was computed from the right value. `docs/bi-rules.md` says this plainly.

#### 19.3 `prumi.format(value, metric, options)`

- `metric` must be a metric name. **Type and unit always come from the metric definition** (R10).
- `options` may only adjust presentation: `{decimals}` and `{compact: true}` (`Intl.NumberFormat` compact notation: `$80K`, `2.7M`).
- A format object, an unknown name or anything else formats as a plain number and logs one console warning per distinct argument (as in section 18).

This replaces section 18 item 4, which let a page hard-code its own unit (`{type: "currency", unit: "USD"}`).

#### 19.4 Visible errors

The runtime wraps every `prumi.on` callback.
- If a callback throws, or a block fails and has no `prumi.onError` handler, the runtime writes a short message ("This part of the dashboard couldn't load.") into the block's bound elements (`presentation.bindings`) instead of leaving them blank or on "Loading…".
- Errors are also included in the browser report (19.1b).

#### 19.5 Network-call warning

`html.network_call` (warning, static): the page's scripts contain `fetch(`, `XMLHttpRequest`, `WebSocket`, `EventSource` or `sendBeacon`. The message says these calls are blocked and data only comes through `prumi.on`.

#### 19.6 Reading back dashboards

Two new MCP tools:

| Tool | Input | Output |
|---|---|---|
| `list_dashboards` | none | `[{id, title, current_version, status, updated_at, url}]` for the dashboards the viewer may read |
| `get_dashboard` | `id`, `version?` (default: current), `include_html: bool = true` | `{id, version, spec, html, status, url, checks}`. `checks` are the latest browser reports (19.1b) |

- **Permissions go through two registry functions,** `can_read(viewer, dashboard)` and `can_edit(viewer, dashboard)`, used by these tools, by the block API and by `publish_dashboard` when it creates a new version of an existing dashboard. In this milestone both return true: there's one local user and no login. Owners, editors and viewers come with login.
- The publish error for a dashboard the viewer may not edit is reserved: `dashboard.forbidden`.

#### 19.7 Editing a candidate or a published version

`validate_spec` also accepts `base` + edits instead of the full content:

```
validate_spec(base: candidate_id | {dashboard_id, version},
              spec?: <full spec; default: the base's spec>,
              html_edits?: [{old: "...", new: "..."}])
```

- Edits apply in order. Each `old` must occur exactly once in the HTML as it stands at that point; otherwise `edit.no_match` or `edit.ambiguous` (error, with the edit's index).
- The result is validated like any content and returns a new `candidate_id`.
- A published base needs `can_read`; publishing the result needs `can_edit`.

#### 19.8 Default theme

The frame gets `<link rel="stylesheet" href="<server origin>/theme.css">`, injected before the runtime (`style-src` adds the server origin). It defines CSS variables on `:root` for light mode, and for dark mode under `prefers-color-scheme: dark`:

`--prumi-bg`, `--prumi-surface`, `--prumi-text`, `--prumi-text-muted`, `--prumi-border`, `--prumi-accent`, `--prumi-positive`, `--prumi-negative`, `--prumi-series-1` … `--prumi-series-8`, `--prumi-font`, `--prumi-font-mono`, `--prumi-radius`.

- Text colors reach a contrast of at least 4.5:1 against `--prumi-bg` and `--prumi-surface` in both modes (tested).
- The shell uses the same variables. The page follows the OS setting; it may ignore the theme.

#### 19.9 Guide and instructions

`authoring_guide` gains:
- dashboard blocks allow 5,000 rows (the 200-row cap applies only to `query`);
- `total` on a block with `time_grain` is one grand total for the window, not one per period (use a block without dimensions for per-period totals);
- layout math (scales, widths, color ranges) is allowed; showing a number Prumi didn't deliver isn't, with the provenance rules of 19.1 and the `data-prumi-role="axis"` marker;
- no `<canvas>`;
- the theme variables;
- `prumi.format` options;
- editing with `base` + `html_edits`;
- "if you can open a browser, take one screenshot of the draft at desktop width; don't read the page's text".

The server instructions gain:
- in step 3: "Send the HTML straight to `validate_spec`; don't write it to a local file first."
- a step 0: "When the user wants a dashboard or chart about company metrics, use Prumi; don't copy its data into other tools. To change an existing dashboard, use `list_dashboards` and `get_dashboard`, then edit it with `validate_spec(base=...)`."

#### 19.10 Server shutdown

Ctrl+C or SIGTERM stops the server within 5 s even while MCP clients or browsers are connected: uvicorn `timeout_graceful_shutdown=5`, open MCP streams are closed, and Chromium is shut down.

#### 19.11 Chromium is a server dependency

`playwright` moves from the dev dependencies to `prumi-core`'s dependencies. `prumi serve` checks at startup that Chromium is installed; if it isn't, it fails with a message giving the install command (`uv run playwright install chromium`). The README's quick start includes that step. Without Chromium the server doesn't start, because publishing would no longer check numbers.

#### 19.12 Docs

- `docs/bi-rules.md`: R8 explains provenance (19.1), its two checks and what they can't see (visual encoding); `R8.unverified_number`, `html.canvas` and the other new codes are listed.
- README: "Restart your agent sessions after upgrading Prumi". Open sessions keep the tool list and instructions they loaded at the start.

#### 19.13 Tightening after the proof (added and accepted 2026-10-18)

The provenance proof (task 34) worked, and showed four gaps:
- a hard-coded number that's correct today passes at publish;
- text the scan can't read (closed shadow roots, nested frames, images);
- evenly spaced fake numbers pass as axis ticks;
- a low-precision number matches by chance one of the many values on a page. A JavaScript-computed weekly average shown as `$228K` often equals some week's rounded revenue.

**1. Copied numbers (`R8.copied_number`, error).** At the server check, the stored HTML source is scanned for numbers equal to a value delivered in the dry run: in text with 3+ significant digits, in script literals with 4+. Any match was copied into the page instead of coming through `prumi.on`. Honest pages render data numbers from callbacks, so their source holds none. Round layout constants like `1200` have few significant digits and don't count; attribute values (e.g. SVG coordinates) are ignored.

**2. Every number belongs to a bound block (`R8.unbound_number`, error).**
- Every number shown, other than the allowed non-data numbers of 19.1, must sit inside an element matched by a `presentation.bindings` selector, and must match a value of **that binding's block or blocks**. The nearest bound ancestor wins.
- A binding's value may be a block id or a list of block ids (`"#table": ["regions", "regions_total"]`).
- A number outside any bound element is flagged with its element path and the hint "bind this element to the block it shows". So are tooltips appended outside a bound element.
- This applies to both checks (server and browser) and replaces matching against every value on the page.

**3. Nothing the scan can't read.**
- `<iframe>` (`html.iframe`), `attachShadow({mode: "closed"})` (`html.closed_shadow_root`) and images (`html.image`: `<img>`, CSS `background-image`, `<image>` in SVG) are errors, found statically and by the server check.
- The frame's CSP sets `img-src 'none'`. Icons work as inline SVG.

**4. Unmarked axis ticks only in SVG.** The rule that 3+ evenly spaced round numbers count as ticks without `data-prumi-role="axis"` applies only inside `<svg>` charts. Elsewhere, tick-like numbers need the marker.

**5. When a published dashboard gets flagged later.** When a browser report (19.1b) flags a published version, for example because the data changed or for a filter state the server didn't check:
- the shell shows a bar to that viewer: "This page shows numbers Prumi didn't deliver.", with the list on click;
- the version is marked `needs_attention` in `list_dashboards`, `get_dashboard` and the status API, until a new version is published or a later report for the same filter state is clean.

This replaces 19.1b's "nothing is shown to viewers": publishing can't catch what only appears after publishing.

**6. Hover pass (added 2026-10-18).** Tooltips often appear only on hover, often in an element appended to `<body>`. Without hovering, the server check never sees them, and an unbound tooltip would trigger the viewer bar (rule 5) on every hover of an honest page. So after the first scan, the server check hovers a sample of chart marks: up to 20 elements under bound elements that have hover listeners, or SVG shapes inside bound charts. After each hover it scans the newly visible text with the same rules. A number in a tooltip outside a bound element gives `R8.unbound_number`, with the hint "render the tooltip inside the bound element or bind it to the blocks it shows". This adds at most about 1 s to a check.

**Details settled during implementation (2026-10-18):**
- A bound tooltip needs a static element in the HTML (`<div id="tip" hidden></div>`), because bindings select existing ids.
- The viewer bar and `needs_attention` count flagged numbers only; canvas and runtime errors still block certification.
- Only the current version can need attention; older versions report false.
- The hover pass skips marks that aren't rendered, waits one animation frame per hover, and moves the mouse away at the end.

**Fixtures:** the first-run dashboard fixture gets bindings in its spec, so it stays a must-pass page under rule 2. **The second-run dashboard is removed from the fixtures** (decided 2026-10-18, question 3): it was written before these rules, and its table shows another block's values without declaring it. The real agent page from the benchmark run (task 46), written with the new guide, is added as a must-pass fixture; if it fails, that goes to `questions.md`. Add must-fail fixtures for:
- a correct-today hard-coded number;
- a JavaScript average that coincides with a bucket value at the precision shown;
- an image with numbers in it;
- an evenly spaced fake series outside SVG.

#### 19.14 Polish from the third run (added and accepted 2026-10-18)

The third agent run (2026-10-18) followed the rules from the guide and never triggered a number-check code. These five changes come from what it ran into.

**1. Failed validations return an id too.**
- `validate_spec` returns a `candidate_id` for every call that has a spec and HTML, valid or not, with `ok` saying which.
- Any candidate can be the `base` of an edit, so a page that failed can be fixed with `html_edits` instead of being resent.
- Only a valid candidate can be published; publishing an invalid one gives `publish.invalid_candidate` (error).
- `base` also accepts a plain dashboard id, meaning its current version.
- A `base` of any other shape gives `validate.invalid_base`, with the message "base must be a candidate_id from validate_spec, a dashboard id, or {dashboard_id, version}".

**2. Runtime errors say where.** `html.runtime_error` gives the line and column in the submitted HTML, and up to 5 lines of the stack, for errors thrown by the page's inline scripts.

**3. Axis ticks at any precision.**
- Inside `<svg>` or an element marked `data-prumi-role="axis"`, 3 or more **evenly spaced** numbers count as ticks whatever their precision: `$280, $286, $292` is a valid AOV axis.
- The "at most 2 significant digits" limit stays only for single tick-like numbers in axis-marked elements.
- Outside SVG and axis-marked elements, nothing counts as a tick (19.13 rule 4).

**4. Check more filter states before publishing.**
- `validate_spec` accepts `check_states`: up to 5 filter states (`{"filters": {"<filter id>": <value>}}`).
- Each one is rendered and checked like the default state, and its issues name the state.
- The states are stored with the candidate, and publishing re-checks them.

**5. Guide additions:**
- the exact text patterns that aren't data: dates, years, period counts such as "Last 26 weeks", numbers from the spec or result metadata, ticks;
- ordering or sorting by a value computed in JavaScript is layout and is allowed, as long as the computed value isn't shown;
- `total` and `others` carry no comparison values in this release;
- `check_states`, and editing a failed candidate.

**Fixture:** the third-run dashboard (`~/test3/region-revenue-aov-weekly.html` and `.spec.json`) is added as the real must-pass fixture decided in question 3.

**Details settled during implementation (2026-10-18):**
- Tick runs (v, v+d, v+2d) are found among all of a chart's numbers, not by requiring every number to be evenly spaced, because charts also hold data labels and titles. Known limit: a made-up number that completes a run of three evenly spaced values in the same chart passes.
- An unknown filter in `check_states` gives `spec.unknown_filter` at `check_states[i].filters.<id>`; bad filter values come back as `query.failed`, named with the state. States run only when the static checks pass.
- A candidate edited from another keeps its `check_states` unless the edit passes its own; a published or dashboard-id base starts with none.
- `base` accepts any JSON value in the tool schema, so a wrong shape gets `validate.invalid_base` instead of the SDK's schema error.
- Runtime error columns are those of the failing operation, as the browser reports them.

#### 19.15 Layout report from validation (added and accepted 2026-10-18)

None of the three agent runs saw the page it built, and number checks don't catch overlapping labels, clipped text or a page wider than the screen. The server already renders the page during validation (19.1), so it measures the render and reports layout problems as warnings. **No image and no text from the page is returned:** only problem kinds, element paths and box geometry. No dashboard data reaches the agent.

| Code | Severity | When |
|---|---|---|
| `layout.text_overlap` | warning | Two visible text elements overlap by more than 20% of the smaller one's area (an element and its own ancestors don't count) |
| `layout.text_clipped` | warning | Text overflows its box and is cut off: `overflow: hidden`, an ellipsis, or outside an SVG's viewBox |
| `layout.too_wide` | warning | The page is wider than the viewport (horizontal scrolling) |
| `layout.small_text` | warning | Visible text smaller than 10 px |
| `layout.empty_block` | warning | A bound element renders with zero size or no visible content |

- Each issue gives the element path(s) (tag, id, classes) and their boxes (`x, y, width, height`). **Never** text content, numbers or attribute values.
- At most 20 issues per check. The default render is desktop (1280×800). `validate_spec(..., layout_devices: ["desktop", "phone"])` adds a phone render (390×844).
- The report runs for the default state and for each `check_states` entry (19.14). Warnings don't block publishing.
- Guide: read the layout warnings after each validation and fix them with `html_edits`; ask the user to look at the draft link for anything a measurement can't judge.

#### 19.16 No company data reaches the agent (added and accepted 2026-10-18)

**Prumi never sends company data to the agent.** The agent sees metric definitions, the shape of results, codes and element paths. The numbers and names appear only in the dashboard, in the viewer's browser. This replaces the 200-row cap: `query` returns a profile instead of rows.

**1. `query` returns a profile.** Same input as before; the output has no values:

```json
{
  "block": "query",
  "row_count": 96,
  "window": {"start": "2026-07-20", "end": "2026-10-11", "grain": "week", "partial": false},
  "freshness": {"revenue": "2026-10-14"},
  "columns": [
    {"name": "customer__region", "kind": "dimension", "label": "Region",
     "distinct": 8, "nulls": 12, "label_length": {"min": 4, "max": 13}},
    {"name": "metric_time", "kind": "time", "label": "Time", "distinct": 12},
    {"name": "revenue", "kind": "metric", "label": "Revenue", "format": {"type": "currency", "unit": "USD"},
     "nulls": 0, "sign": "positive", "magnitude": {"min": 4, "max": 5}, "decimals": 2}
  ],
  "has_total": true, "has_others": false, "has_comparison": true,
  "warnings": []
}
```

- `magnitude` is the range of integer-digit counts of the column's values (4–5 digits means 1,000–99,999). It's the one statistic derived from values, kept because agents need it to design axes.
- `sign` is `positive`, `negative`, `mixed` or `zero`; `decimals` is the most common number of decimals shown by the format.
- `label_length` gives the shortest and longest label in characters, for layout.
- The 200-row cap and the `truncated`/`total` fields of section 9's agent output are gone.

**2. Nothing agent-facing echoes page text or data:**
- Number-check issues (`R8.*`), layout issues and the browser reports in `get_dashboard` give codes, element paths and counts. They never give the text or number found.
- Errors coming from the source (`query.failed`, the 502 of the block API as seen through MCP) give the error type and a generic message, never the database's own message text, which can contain values.

**3. Allowed in agent output:** metric and dimension names, labels and descriptions from the catalog; counts; magnitudes; window and freshness dates (calendar facts); spec and issue text the agent itself wrote.

**4. Wording.** The README's "Where your data goes", the guide and the server instructions say: *"Prumi never sends company data to your agent: `query` shows the shape of the data, and the numbers appear only in the dashboard."* Instruction step 2 becomes "Use `query` to learn the shape of the data (how many rows, empties, label lengths, magnitudes) while designing."

**Details settled during implementation (2026-10-18):**
- Bound elements that aren't rendered (a hidden tooltip, `display: none`) don't get `layout.empty_block`; screen-reader-only text is ignored by the layout checks.
- The phone render measures layout only; number checks run on the desktop render.
- In `get_dashboard`'s browser reports, time-filter windows stay (calendar facts) and dimension-filter values become `{"selected": n}`. `prumi certify`, used by the data team, still shows full reports.
- `html.runtime_error` removes the page's dimension values and any 3+-digit number from the browser's message (positions and stack stay), and `html.blocked_request` names only the origin, since a page can put data in both.
- `row_count` says "more than 5000" past the engine's cap.

### 20. Home page (added and accepted 2026-10-18)

`GET /` is the first thing a user sees, and today it's a bare table of links. It should look like a small product, not a debug page. It doesn't need to be elaborate.

**Layout, top to bottom:**
1. **Header:** the Prumi wordmark (text, no image), and the connected source in one line: adapter (`dbt`), the project name from `[source] project` in `prumi.toml` (left out when not set), connection type (`duckdb`), and "data through <date>" (the latest freshness of any metric, computed once at startup and refreshed at most every 10 minutes). Adapter and connection type come from a new adapter member `describe() -> SourceInfo(adapter, connection)`.
2. **Dashboards**, as a responsive grid of cards (one column on a phone). Each card shows:
   - the title, as a link to `/d/<id>`;
   - the status as a badge: `Draft`, `Certified by <name>`, or `Needs attention` (19.13 rule 5), using the theme's colors;
   - the current version and "updated <relative time>" (exact timestamp in a `title` attribute);
   - the metric labels the dashboard uses (from its spec), at most 4, then "+N".

   The cards are sorted by last update, newest first. A search box filters them by title, id or metric label, in the browser, over the page's own text.
3. **Empty state** (no dashboards): a short explanation ("Ask your AI agent to build a dashboard; it appears here as a draft") and the connect command for Claude Code, with a copy button.
4. **Metric requests**, a short list below the dashboards: the open requests' descriptions and dates, newest first, at most 10, then "and N more (`prumi requests`)". Hidden when there are none.
5. **Footer:** the Prumi version and a link to the docs (the repository README for now).

**Rules:**
- **No metric values anywhere on this page.** Titles, labels, counts, dates and statuses only.
- Uses the default theme variables (19.8), in light and dark mode; one small stylesheet `/home.css`. No external fonts or scripts.
- The search box and copy button are one small script `/home.js`. The page's CSP is `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, with no inline script or style.
- Every agent-written string (titles, metric request descriptions) is HTML-escaped.
- Only dashboards the viewer may read appear (`can_read`, 19.6).

**Details settled during implementation (2026-10-18):**
- "Open" metric requests are all of them: nothing in this milestone closes one.
- Badge precedence: `Needs attention`, then `Certified by <name>`, then `Draft` (theme colors negative, positive, muted).
- "Updated" is when the current version was created, from the registry's clock (`Registry.now()`). Relative times: "just now", "N minutes/hours ago", "yesterday", "N days/months/years ago".
- Ties in the newest-first order are broken by id. Metric labels follow their first use in the spec; the labels behind "+N" go in its `title`, and search covers them.
- "Data through" is the latest day with data across all catalog metrics, for the local viewer, computed in `serve()` before binding and cached for 10 minutes. A metric whose query fails is skipped and logged by error type only.
- Each card shows the dashboard id (monospace), so search over the page's text can match ids.
- The connect command uses the configured host and port: `claude mcp add --scope user --transport http prumi http://<host>:<port>/mcp`.
- The docs link is `https://github.com/prumihq/prumi#readme`.
- `home.css` and `home.js` live in `packages/prumi-runtime/src` next to `shell.js` and ship in the wheel; the page also loads `/theme.css` for the variables.
- Without the clipboard API, the copy button selects the command and says "Selected".

### 21. One display locale (added 2026-10-18, draft)

The fourth agent run's page passed validation and was flagged `needs_attention` by its first viewer. `prumi.format` formats with the browser's language, the server check renders in `en-US`, and the number reader (19.1) knows only English separators and `K`/`M`/`B`. In a `pt-BR` browser, `$47K` becomes `US$ 47 mil` and `$2,742,409.35` becomes `US$ 2.742.409,35`; the browser check can't match them, so every dashboard is flagged by its first non-English viewer, certified ones too. Pages that format with the browser's language themselves (`toLocaleString()`, `Intl.NumberFormat(undefined, …)`) fail the same way: the second- and third-run pages did.

**Rules:**
1. **One locale per server**, `[server] locale` (section 2), default `en-US`.
2. **Runtime:** `prumi.format` uses that locale. The runtime also exposes it as `prumi.locale` (a string), so a page can pass it to its own `Intl` calls for dates and other text.
3. **Number reader:** the server check and the browser check read numbers in that locale. Decimal and group separators come from `Intl.NumberFormat(locale).formatToParts`; the compact suffixes for thousand, million and billion come from formatting `1e3`, `1e6` and `1e9` with `notation: "compact"` (and with `style: "currency"`, which uses different words in some locales). Matching is otherwise unchanged (19.1, 19.13). With `en-US`, behavior is exactly as before.
4. **Server check:** renders in the configured locale (instead of the fixed `en-US`). Then it renders the default state once more with a different browser language (`de-DE`, or `en-US` when the configured locale is `de-*`). If the text of any bound element differs between the two renders, validation fails with `R8.browser_locale` (error): "This page formats with the viewer's browser language, so viewers would see different numbers than the check. Use `prumi.format` for numbers and `prumi.locale` for other `Intl` calls." The message names element paths only (19.16).
5. **Guide and server instructions:** numbers only through `prumi.format`; dates and other `Intl` text with `prumi.locale`; never `toLocaleString()` or `Intl.*(undefined, …)`.

Not in this section: showing each viewer numbers in their own browser's language. One server shows numbers one way to everyone.

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
| Canvas charts (not allowed; their text can't be checked) | Later: a solution to find |
| Owners, editors and viewers for dashboards | Installable (with login) |
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
26. An MCP client over Streamable HTTP lists exactly the nine tools (the six of section 14, `authoring_guide` from section 18, `list_dashboards` and `get_dashboard` from section 19), and the server instructions match section 14 as amended by sections 18 and 19.
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

**Trusted numbers, editing and the second-run fixes (section 19)**
38. The provenance fixtures give their expected results through the server check: every "must fail" page gets `R8.unverified_number` (with the offending text), every "must pass" page passes.
39. A page with `<canvas>`, a page that throws on load, and a page that never settles give `html.canvas`, `html.runtime_error` and `html.render_timeout`; `publish_dashboard` refuses all three.
40. The server check reads the page from Playwright's isolated world: a fixture page that overrides `window.prumi` or tampers with the DOM API after rendering still gets its fake number flagged.
41. *(browser test)* After a filter change that makes the page show a number Prumi didn't deliver, the shell posts a report, `get_dashboard` returns it, and `prumi certify` refuses that version and names the filter state.
42. `prumi.format(v, "revenue", {compact: true})` gives a compact currency string; `prumi.format(v, {type: "currency", unit: "EUR"})` gives a plain number and one warning.
43. *(browser test)* A callback that throws, and a failing block without `onError`, show the error message in their bound elements.
44. `fetch(` in a script gives `html.network_call`; a blocked request during the server check gives `html.blocked_request`.
45. `list_dashboards` and `get_dashboard` return the published dashboards, versions, spec, HTML and checks; `can_read`/`can_edit` are called by them, by the block API and by publish (tested with a stub that returns false, giving `dashboard.forbidden`).
46. `validate_spec(base=..., html_edits=[...])` produces the same candidate as sending the edited content; `edit.no_match` and `edit.ambiguous` carry the edit's index.
47. `/theme.css` defines every variable of 19.8 in both modes; the contrast test passes; the frame and shell load it.
48. With an MCP client connected and a browser open, SIGINT and SIGTERM each stop the server within 5 s.
49. `prumi serve` without Chromium fails at startup with the install command.
50. The guide and the instructions contain the additions of 19.9; `docs/bi-rules.md` and the README contain those of 19.12.
51. *(review)* Benchmark: a fresh agent session with the README's prompt. Record the time to publish and the output tokens, and compare with the two earlier runs (360 s / ~46k and 373 s / ~42k).
52. The 19.13 must-fail fixtures each fail with their code (`R8.copied_number`, `R8.unverified_number` or `R8.unbound_number`, `html.image`), and every must-pass fixture still passes.
53. A page whose source contains a delivered value with 3+ significant digits in text gives `R8.copied_number`; a layout constant like `1200` in a script doesn't.
54. A number outside any bound element gives `R8.unbound_number` with its element path; a binding with a list of blocks accepts numbers from each.
55. `<iframe>`, a closed shadow root and each image form give their errors, and the frame's CSP has `img-src 'none'`.
56. Evenly spaced round numbers count as ticks inside `<svg>` without the marker, and are flagged outside it.
57. *(browser test)* A published version whose browser report flags a number shows the bar to that viewer and is marked `needs_attention` in `list_dashboards` and `get_dashboard`, until a clean report or a new version.
58. A fixture page whose tooltip shows block values in an unbound element appended to `<body>` gives `R8.unbound_number` from the server check's hover pass; the same page with the tooltip bound passes; the hover pass adds at most about 1 s.
59. A validation with errors returns a `candidate_id`; editing it with `html_edits` gives a new candidate; publishing an invalid candidate gives `publish.invalid_candidate`; a plain dashboard id works as `base`; any other shape gives `validate.invalid_base`.
60. A page that throws in an inline script gets `html.runtime_error` with the line and column in the submitted HTML.
61. Evenly spaced 3-digit ticks (`$280, $286, $292`) pass inside `<svg>` and inside an axis-marked element, and are flagged in plain HTML.
62. `check_states` renders and checks each state; an issue that appears only in one state names it; publishing re-checks the stored states.
63. The guide contains the 19.14 additions.
64. The third-run dashboard passes as a must-pass fixture.
65. A fixture page with overlapping labels, clipped text, a too-wide element, 8 px text and an empty bound element gets each `layout.*` warning once; a clean page gets none.
66. No `layout.*` issue contains text content, numbers or attribute values from the page (a test with a sentinel value in every text node checks it).
67. `layout_devices: ["phone"]` adds the phone render's issues; publishing isn't blocked by layout warnings.
68. `query` returns the profile of 19.16 and no values: a test with a sentinel value in the demo data checks that it appears nowhere in any MCP tool output (`query`, `validate_spec`, `publish_dashboard`, `get_dashboard`, `list_dashboards`, errors).
69. `magnitude`, `sign`, `decimals`, `distinct`, `nulls` and `label_length` are correct for the demo's revenue-by-region and revenue-by-region×week queries.
70. A page showing an unverified number gets `R8.unverified_number` with the element path and no number in the message; a failing source query's error contains no database message text.
71. The README, guide and instructions contain the 19.16 wording; nothing mentions the 200-row cap.
72. The home page shows the header with source and freshness, one card per readable dashboard with title, status badge, version, relative update time and metric labels, sorted newest first, and the open metric requests.
73. With no dashboards, the empty state and the connect command appear; with no open metric requests, that section is hidden.
74. A dashboard title containing `<script>` and a metric request containing HTML are shown as text (escaping test); the page's CSP is as in section 20 and the page has no inline script or style.
75. *(browser test)* The search box filters the cards; the copy button copies the connect command; at 390 px width the cards form one column with no horizontal scrolling; light and dark mode both apply the theme.
76. The home page contains no metric values (the sentinel test of AC 68 covers `GET /`).
77. *(browser test)* With `locale = "pt-BR"`, `prumi.format(47123, "revenue", {compact: true})` gives `US$ 47 mil` in a browser whose language is `en-US`; with `locale = "en-US"`, it gives `$47K` in a `pt-BR` browser. `prumi.locale` returns the configured tag.
78. With `locale = "pt-BR"`, the server check accepts `US$ 47 mil` and `US$ 2.742.409,35` for delivered `47123` and `2742409.35`, and refuses `US$ 48 mil`. All existing `en-US` number-check tests pass unchanged.
79. *(browser test)* The fourth-run page (copied into the fixtures), published under `en-US` and opened in a `pt-BR` browser, is not flagged.
80. A page that formats a bound number with `toLocaleString()` fails validation with `R8.browser_locale`; the same page using `prumi.format` passes.
81. An unknown `locale` stops `prumi serve` at startup with a message naming the setting.
