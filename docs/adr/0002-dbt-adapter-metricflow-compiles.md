# 0002. dbt adapter: MetricFlow compiles, Prumi executes

Status: Accepted · 2026-10-17

## Context

The dbt adapter has to answer metric queries without re-implementing metric definitions, and run them with each viewer's own credentials so the warehouse enforces access. That only works if Prumi owns the database connection and MetricFlow only produces SQL. The [`metricflow-compile` spike](../../spikes/metricflow-compile/README.md) tested this with MetricFlow 0.213.0 (Apache-2.0) on DuckDB. Ratio and distinct-count metrics, filters and time windows all matched hand-written SQL, and MetricFlow never ran any SQL.

## Decision

- The dbt adapter reads `target/semantic_manifest.json` and builds a `MetricFlowEngine` in-process from it. It does not run dbt and does not depend on dbt-core; its only MetricFlow-side dependency is `metricflow`.
- The engine gets a **compile-only SQL client**: it provides the dialect (`sql_engine_type`, `sql_plan_renderer`) and raises if MetricFlow tries to query, execute or dry-run.
- The adapter uses `engine.explain(...)` to compile and runs the SQL itself over a connection opened with the viewer's credentials. It never calls `engine.query(...)`.
- Every import from MetricFlow lives in one module of `prumi-adapter-dbt`, and the `metricflow` version is pinned exactly. The engine class is not a stable public API; each version bump reruns the spike's checks and rechecks the license.
- **Prumi's engine owns time windows.** It computes each window and snaps it to full periods of the query grain before compiling (partial periods are handled by the engine's own rules), so MetricFlow only receives grain-aligned windows.
- The catalog comes from `list_metrics`, `list_dimensions` and `list_group_bys`. Coarser time grains are derived from each time dimension's base grain. Formats and units come from a `config.meta` convention, to be specified.

## Consequences

- Access control stays with the warehouse, and the adapter needs no MetricFlow connection config.
- The compiled SQL names relations as dbt's target resolved them at parse time (database.schema.table). A viewer's connection must resolve those same names; per-viewer schemas or databases are not supported.
- MetricFlow snaps windows to the query grain (a mid-month window queried by month returns whole months). Because the engine only sends grain-aligned windows, this never changes numbers silently.
- Only warehouses in MetricFlow's `SqlEngine` list can be compiled for.
- The semantic manifest is written by the user's dbt version and read by Prumi's pinned MetricFlow. Prumi must declare which dbt versions it supports and fail with a clear error on a manifest it can't read.
- MetricFlow upgrades may break the adapter without a deprecation period; the exact pin and a contract test based on the spike's checks contain that.
