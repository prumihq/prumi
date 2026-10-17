# Spike: MetricFlow compiles, Prumi executes

Status: done, 2026-10-17 · Time box: 1–2 sessions

## Question

Can we use MetricFlow **as a Python library** to compile a semantic query (metrics + group by + filters) from a dbt project's semantic manifest into SQL, **without MetricFlow running the query**, and then execute that SQL ourselves over our own DuckDB connection?

## Why it matters

The dbt adapter's design rests on this split: **MetricFlow compiles, Prumi executes, the warehouse enforces access.** Later, Prumi will run queries with each viewer's own credentials, so the connection must belong to Prumi, never to MetricFlow. If MetricFlow can't hand us the SQL without owning the connection, the adapter design changes before any product code is written.

## Constraints

- Use MetricFlow's **Python API**. No `mf` CLI subprocess, no dbt Cloud Semantic Layer API.
- MetricFlow may only **compile**. Our code opens the DuckDB connection and executes the SQL.
- If MetricFlow insists on a SQL client even just to compile (e.g. to know the dialect), check whether a client that never executes anything works. Record exactly what it needs.
- Everything lives in this folder: its own `pyproject.toml`, its own tiny dbt project, its own DuckDB file. Don't touch `packages/`, and don't add MetricFlow or dbt to the workspace.
- Build a **tiny** dbt project here, on DuckDB, with seed data you write by hand (a few dozen rows). This is not `examples/demo-dbt-project`; that one comes in the MVP.
- Use the newest released MetricFlow and dbt-core versions that work together. A different Python version for this folder is fine if needed.

## What to build

A tiny dbt project with:
- one fact table (e.g. orders: date, region, amount, customer id) and one dimension if needed
- a semantic model with at least:
  - a **simple metric**, e.g. `revenue` (sum of amount)
  - a **ratio metric**, e.g. `average_order_value` (revenue / order count)
  - a **distinct count**, e.g. `customers`
- a time dimension at day grain and a categorical dimension (region)

Then a script that:
1. produces the semantic manifest (`dbt parse` is fine for the spike)
2. loads it into MetricFlow and compiles queries to SQL
3. executes the SQL over its own DuckDB connection and prints the rows

## Success criteria

The answer is **yes** if all of these hold:

1. We get compiled SQL from MetricFlow without MetricFlow executing anything.
2. Our own connection runs that SQL and returns correct rows.
3. The ratio metric queried **by month** and **by region** each match a hand-written SQL check. That proves it's computed at the displayed grain and not re-aggregated from finer rows. Same check for the distinct count.
4. A filter (e.g. `region = 'North'`) and a time window compile and return correct rows.

## Also record

- What metadata we can read for a catalog: metric names, descriptions, labels, types, valid dimensions per metric, time grains, formats/units if any.
- Exact versions of MetricFlow, dbt-core and dbt-duckdb, and **the license of each exact version** (MetricFlow's license has changed in the past; confirm the one we'd depend on).
- What MetricFlow needs at runtime: files, objects, a SQL client or not, network access or not.
- Anything surprising, slow or awkward.

## When done

1. Fill in **Answer** below: yes / no / yes with caveats, plus the evidence (queries, SQL, outputs).
2. Update the row in `spikes/README.md`.
3. If the answer shapes the adapter design, propose an ADR in `docs/adr/` (status: Proposed).
4. If you get stuck or the question turns out to be wrong, stop and write what you found and what's blocking.

## Answer

**Yes, with caveats.** MetricFlow 0.213.0 compiles semantic queries to DuckDB SQL from the dbt semantic manifest without running anything. Our own DuckDB connection executes the SQL, and every check matches hand-written SQL. Caveats are listed below; none of them blocks the design.

Run it: `cd spikes/metricflow-compile && uv run python spike.py` (about 6 s, mostly dbt). It exits 0 only if every check passes.

### How it works

- `dbt_project/`: 36 hand-written orders (Jan–Mar 2026, regions North/South/East, 17 customers who repeat across months and regions), an `orders` view, a daily time spine, one semantic model with `revenue` (simple, sum), `orders` (simple, sum of 1), `average_order_value` (ratio of revenue / orders) and `customers` (simple, count distinct). Dimensions: `order_date` (time, day grain) and `region` (categorical).
- `spike.py`:
  1. runs `dbt build` and `dbt parse` in a subprocess, which writes `target/semantic_manifest.json`;
  2. `parse_manifest_from_dbt_generated_manifest(json)` → `SemanticManifestLookup` → `MetricFlowEngine(lookup, CompileOnlySqlClient())`;
  3. `engine.explain(MetricFlowQueryRequest.create(...)).sql_statement.sql` gives the SQL; `duckdb.connect(path, read_only=True)` runs it.

`CompileOnlySqlClient` implements MetricFlow's `SqlClient` protocol. It returns `SqlEngine.DUCKDB` and `DuckDbSqlPlanRenderer()`; `query`, `execute` and `dry_run` record the call and raise.

### Success criteria

1. **Compiled SQL without MetricFlow executing anything: yes.** `explain()` only reads `sql_engine_type` and `sql_plan_renderer` from the client. After all queries the client had recorded zero calls. As a control, `engine.query()` does call `client.query()` and is refused. No bind parameters were produced for any query (filters are inlined as literals).
2. **Our own connection returns correct rows: yes.** It is a read-only DuckDB connection that MetricFlow never sees.
3. **Ratio and distinct count at the displayed grain: yes.** Every row matches hand-written SQL:

   | Grain | AOV (MetricFlow = hand SQL) | Customers |
   |---|---|---|
   | 2026-01 | 135.229167 | 8 |
   | 2026-02 | 111.499091 | 10 |
   | 2026-03 | 110.768462 | 12 |
   | East | 142.999091 | 8 |
   | North | 98.035 | 7 |
   | South | 122.159091 | 6 |
   | Total | 119.145278 | 17 |

   Re-aggregating finer rows gives wrong numbers, so the matches mean something: the average of daily AOVs is 116.68 (true: 119.15), and summing customers gives 30 by month or 36 by day (true: 17). MetricFlow computes the ratio from the numerator and denominator aggregated at the requested grain, `CAST(revenue AS DOUBLE) / CAST(NULLIF(orders, 0) AS DOUBLE)`, and runs `COUNT(DISTINCT customer_id)` at that grain.
4. **Filter and time window: yes.** `where_constraints=["{{ Dimension('order__region') }} = 'North'"]` with `time_constraint_start=2026-02-01`, `time_constraint_end=2026-03-31`, by month, matches (Feb: revenue 220.50, AOV 44.10, 4 customers; Mar: 546.99, 136.7475, 4). A day-grain query by day × region, combining a Jinja filter on `metric_time` with an end-of-window constraint, also matches.

Compiled SQL for the ratio + distinct count by month (descriptions stripped):

```sql
WITH sma_10001_cte AS (
  SELECT
    DATE_TRUNC('month', order_date) AS metric_time__month
    , amount AS __revenue
    , 1 AS __orders
    , customer_id AS __customers
  FROM "spike"."main"."orders" orders_src_10000
)
SELECT
  COALESCE(subq_5.metric_time__month, subq_10.metric_time__month) AS metric_time__month
  , MAX(subq_5.revenue) AS revenue
  , MAX(subq_5.orders) AS orders
  , MAX(subq_5.customers) AS customers
  , MAX(CAST(subq_10.revenue AS DOUBLE) / CAST(NULLIF(subq_10.orders, 0) AS DOUBLE)) AS average_order_value
FROM (
  SELECT metric_time__month, SUM(__revenue) AS revenue, SUM(__orders) AS orders
       , COUNT(DISTINCT __customers) AS customers
  FROM sma_10001_cte GROUP BY metric_time__month
) subq_5
FULL OUTER JOIN (
  SELECT metric_time__month, SUM(__revenue) AS revenue, SUM(__orders) AS orders
  FROM sma_10001_cte GROUP BY metric_time__month
) subq_10
ON subq_5.metric_time__month = subq_10.metric_time__month
GROUP BY COALESCE(subq_5.metric_time__month, subq_10.metric_time__month)
```

The time window compiles to `WHERE metric_time__day BETWEEN '2026-02-01' AND '2026-03-31'`, applied before the region filter and the aggregation.

### Catalog metadata

The data comes from `engine.list_metrics()`, `engine.list_dimensions()` and `engine.list_group_bys(metric_names)`.

- **Metrics:** name, label, description, type (`simple`, `ratio`, …), `type_params` (aggregation, ratio numerator/denominator, input measures), `config.meta`, `filter`, semantic models used, and the dimensions valid for each metric.
- **Dimensions:** `dunder_name` (the query name, e.g. `order__region`), name, label, description, type (categorical/time), base grain for time dimensions, entity links. Entities come from `entities_for_metrics`.
- **Time grains:** only the base grain is listed (`metric_time__day`). Coarser grains (`__week`, `__month`, `__quarter`, `__year`) are accepted in queries but not listed, so the catalog has to derive them from the base grain.
- **Formats and units:** there are none in the spec. The only option is a convention in `config.meta` (e.g. `meta: {format: currency, unit: USD}`).
- **Dimension values:** `engine.get_dimension_values()` / `explain_get_dimension_values()` exist; the explain variant should compile the same way (not tested).

### Versions and licenses

All licenses were read from the installed wheels' metadata and LICENSE files.

| Package | Version | License |
|---|---|---|
| metricflow | 0.213.0 | Apache-2.0 (full Apache 2.0 text in the wheel; no Business Source License text). Bundles `metricflow_semantics` and `metricflow_semantic_interfaces`. |
| dbt-core | 1.12.6 | Apache-2.0 |
| dbt-duckdb | 1.11.0 | Apache-2.0 |
| duckdb | 1.5.6 | MIT |
| dbt-core-experimental-parser | 2.0.8 (pulled in by dbt-core) | Apache-2.0 per metadata; the wheel ships only a prebuilt binary |

Python 3.12. metricflow requires `>=3.10,<3.15`. dbt-core 1.12.6 itself depends on `metricflow>=0.213.0,<1.0`, so the two versions are tied together. Older MetricFlow releases used a different license; any version bump must recheck the license.

### What MetricFlow needs at runtime

- **Files:** only `semantic_manifest.json`. It does not need `manifest.json`, the dbt project or dbt-core. This was verified in a clean environment with only `metricflow==0.213.0` (22 packages, no `dbt` module): compiling works with sockets blocked and no database file present.
- **Objects:** a `SemanticManifestLookup` and an object implementing `SqlClient` that provides the dialect (`sql_engine_type`, `sql_plan_renderer`). It needs no connection and no credentials.
- **Network:** none. MetricFlow's telemetry only writes to Python logging. dbt itself sends anonymous usage stats unless they are disabled; the spike sets `send_anonymous_usage_stats: false` and `DO_NOT_TRACK=1`. Prumi won't run dbt anyway.
- **Speed:** importing MetricFlow takes about 0.5–1 s, building the engine about 7 ms for this manifest, and compiling a query 7–25 ms.

### Caveats, surprises, awkward bits

1. **The API is internal.** `MetricFlowEngine`'s docstring says: "TODO: provide a more stable API layer instead of assuming this class is stable." The manifest loader lives in `metricflow_semantics.model.dbt_manifest_parser`. The adapter should pin MetricFlow tightly and keep every MetricFlow call in one module.
2. **The SQL client protocol is wider than what we need.** We implement `query`/`execute`/`dry_run` just to refuse them. A future MetricFlow could start calling the client during compile (e.g. a dry run). The refusing client would catch that loudly instead of silently running SQL.
3. **Relation names are baked into the SQL** as `"spike"."main"."orders"` (database, schema, table from dbt's target at parse time). Prumi's connection must resolve those names: the right DuckDB file name or catalog, and the right warehouse database for other engines. If a viewer's credentials use a different database or schema than the dbt target, the SQL won't resolve.
4. **Time windows snap to the query grain.** A window of Jan 15 – Feb 10 queried by month returns all of January and all of February, rows outside the window included. At day grain the window is exact, and `BETWEEN` is inclusive. Dashboards must not show such a window as exact when they query at a coarser grain.
5. **Output columns are reordered** by default (`LEGACY_TYPE_GROUPED`: dimensions, then simple metrics, then derived and ratio metrics). Read results by column name, or pass `OutputColumnOrderMode.INPUT_ORDER`.
6. **Ratio metrics compile to a self-join:** two aggregated subqueries, a `FULL OUTER JOIN` and `MAX()`. The SQL is correct but heavier than hand-written SQL. The spike didn't look at warehouse cost.
7. **The dialect comes from MetricFlow's own `SqlEngine` enum and renderers** (BigQuery, DuckDB, Redshift, Postgres, Snowflake, Databricks, Trino, Vertica, Athena). A warehouse outside that list can't be compiled for.
8. **dbt-duckdb holds the DuckDB file open in-process** after `dbtRunner.invoke()` returns, and DuckDB then refuses a second, read-only connection. The spike runs dbt in a subprocess. This doesn't affect Prumi, which reads the manifest and never runs dbt.
9. **Bad names give good errors:** `InvalidQueryException` with suggestions (e.g. for `revnue` it suggests `['revenue', …]`). These can be shown to dashboard authors.
10. **dbt 1.12 also writes `target/osi_document.json`** (Open Semantic Interchange v0.1.1, with datasets, fields and metrics). It wasn't used here, but it may be worth a look as a catalog source later.
11. Legacy `measures:` YAML still parses without deprecation warnings on dbt-core 1.12.6. The newer metrics-on-models YAML spec was not tested.
