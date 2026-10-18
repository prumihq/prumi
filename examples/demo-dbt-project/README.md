# Demo dbt project

A stand-in "customer" project: a synthetic online shop on DuckDB, with dbt models, semantic models and metrics. Prumi's tests and the demo run against it. It is not product code and is not part of the uv workspace.

## Build

From the repository root:

```sh
uv run --project examples/demo-dbt-project python examples/demo-dbt-project/build.py
```

This runs `generate.py` (seeded, deterministic raw data in the `raw` schema), then `dbt build` and `dbt parse`. It writes `demo.duckdb` and `target/` (including `target/semantic_manifest.json`), both git-ignored. It's idempotent and takes about 10 seconds. The test suite builds it automatically when the outputs are missing or stale.

`prumi.toml` is the configuration the demo runs with. It pins `as_of = 2026-10-15`, so October 2026 is a partial month and the data ends the day before.

DuckDB names the database after the file, and dbt bakes that name (`demo`) into the relations in the semantic manifest (`"demo"."main"."orders"`). Renaming `demo.duckdb` breaks the queries, which is what Prumi's startup check catches.

## Data

| Raw table | Rows | Notes |
|---|---|---|
| `customers` | 3,000 | 7 regions; about 2% have a NULL `region`. One country is `Côte d'Ivoire` (a quote in a filter value). |
| `products` | 60 | 6 categories of 10 |
| `orders` | about 88,000 | 2024-01-01 to 2026-10-14; growth, a December peak, fewer orders on Sundays; channels `web`, `store`, `marketplace` |
| `order_items` | about 182,000 | 1–5 lines per order, quantities, list prices (up 5% from 2025-07-01) and discounts |
| `inventory_snapshots` | about 61,000 | Units on hand per product at the end of each day |

## Metrics and the traps they plant

Every number Prumi shows has to be computed by the source at the grain it's shown. These metrics give wrong answers if anything re-aggregates them:

| Metric | Kind | Format | Additive | Trap |
|---|---|---|---|---|
| `revenue` | simple, sum | currency USD | yes | |
| `orders` | simple, count | integer | yes | |
| `units_sold` | simple, sum | integer, `units` | yes | |
| `average_order_value` | ratio `revenue / orders` | currency USD | no | **Average of averages.** The mean of monthly AOVs isn't the yearly AOV. |
| `customers` | simple, count_distinct | integer | no | **Summed distinct counts.** A customer who orders in two months is counted twice if months are summed. |
| `gross_margin` | derived `revenue - cost` | currency USD | no | **Conservative additivity.** `a - b` would sum correctly, but Prumi treats every derived metric as non-additive. |
| `gross_margin_pct` | ratio `gross_margin / revenue` | percent | no | **Ratio at grain.** Must be computed from the numerator and denominator at the displayed grain. |
| `units_on_hand` | simple, sum, `non_additive_dimension` on `snapshot_date` (last value) | integer, `units` | no | **Semi-additive balance.** Summing daily stock over a month is meaningless. It also can't be joined to customer dimensions. |
| `discount_rate` | ratio `discounts / gross_sales` | **none declared** | no | **No format:** Prumi warns (R10). |

`cost`, `gross_sales` and `discounts` are simple sums (currency USD, additive) that exist because MetricFlow builds derived and ratio metrics only from other metrics, and this YAML form can't hide a metric. They're ordinary metrics in Prumi's catalog: whether a metric is public is the source's decision.

Dimensions: `customer__region`, `customer__country`, `customer__segment`, `order__channel`, `product__category`, plus `metric_time`. The fact models carry the foreign keys they need, so each dimension is one entity hop away. `units_on_hand` only joins to `product__category`.

Formats come from `config.meta.prumi` on each metric (`format`, `unit`, `decimals`), and owners from `config.meta.owner`.
