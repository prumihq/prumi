# 0006. The catalog shows the source as it is

Status: Proposed · 2026-10-19

## Context

Prumi is only useful if it works on a dbt project as the data team already wrote it, without a configuration project first. A second set of names or definitions next to the source's would drift from it. But dbt's semantic layer describes how a metric is computed, not how it's displayed: nothing says that `revenue` is money in USD, or that `gross_margin_pct` is a fraction to show as a percentage. [ADR 0002](0002-dbt-adapter-metricflow-compiles.md) left that convention to be specified.

## Decision

- **Names are the adapter's own.** Metrics and dimensions are referenced by MetricFlow's names (`customer__region`). Prumi has no naming layer of its own.
- **One name per attribute:** when MetricFlow reaches the same attribute through several join paths, the catalog keeps the shortest for each metric, and names of equal length both stay. Names it drops are refused like unknown ones (R2).
- **Every metric in the manifest is in the catalog.** Whether a metric is public is the source's decision.
- **Formats come from `config.meta.prumi`** on each metric: `format` (`number`, `integer`, `percent` or `currency`), `unit` (an ISO 4217 code for currency), `decimals`. Prumi uses its own key because other tools already read keys like `meta.format` with their own meanings. A missing or invalid entry gives a plain number and the warning `R10.no_format`.
- **The owner** comes from the common `config.meta.owner`.
- **Additivity is conservative.** A metric is additive only if it's simple, its aggregation as written in the project (before MetricFlow rewrites it) is `sum`, `count` or `sum_boolean`, and its measure has no `non_additive_dimension`. A derived metric is non-additive even when it's `a - b`.
- **Pages format with the metric's own format:** `prumi.format(value, metric)` takes a metric name. A page may change the decimals or use compact notation, never the type or the unit.

## Consequences

- A metric looks the same on every dashboard, and a page can't show the wrong currency.
- Teams add a few YAML lines per metric to get formats. Without them dashboards still work, with plain numbers and a warning.
- Wrongly treating a metric as non-additive costs an extra query; wrongly treating it as additive would give wrong totals.
- If the source renames a metric or a dimension, dashboards that use it stop working until they're edited, and `prumi certify` refuses them.
