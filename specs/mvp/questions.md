# MVP: open questions

Found while implementing. Each one says what was found, the options, and which task is blocked.

## 1. Helper metrics that the metrics table doesn't list

**Blocks:** task 9 (catalog extraction), AC 5 ("the demo catalog matches the table in section 15"). Tasks 1–8 don't depend on the answer.

**Found:** MetricFlow's derived and ratio metrics only take **metrics** as inputs, not measures. Two rows of the section 15 table need inputs that aren't among the nine metrics:

- `gross_margin`, "derived revenue − cost", needs a `cost` metric.
- `discount_rate`, "ratio", needs a numerator and a denominator. The demo uses `discounts / gross_sales`, the usual definition, so that's two more metrics.

These can't be hidden with the YAML form the spec uses. With dbt-core 1.12.6 and legacy `measures:` YAML, both `type_params.is_private: true` and `hidden: true` on a metric fail `dbt parse` ("Additional properties are not allowed ('is_private' was unexpected)"). MetricFlow's `list_metrics()` only skips `is_private` metrics, which in this YAML form only MetricFlow itself creates.

So the demo's semantic manifest has **twelve public metrics**: the nine in the table plus `cost`, `gross_sales` and `discounts` (simple sums, currency USD, additive). They're in `examples/demo-dbt-project/models/schema.yml` now, because no valid manifest is possible without them.

**Options:**

- **A. The catalog shows all twelve.** Section 15 lists the three helpers (simple, sum, currency USD, additive) and AC 5 checks twelve metrics. No new mechanism. Cost of goods sold, gross sales and discounts are reasonable metrics in their own right.
- **B. Prumi hides metrics marked in `config.meta`**, e.g. `meta: {prumi: {hidden: true}}`. That's a new catalog feature. The spec would also have to say whether a hidden metric can still be queried or put in a dashboard spec, and what `find_metrics` and R7 do with it.
- **C. Redefine the two metrics without helpers.** Not possible for "revenue − cost" as a derived metric. `gross_margin` could become a simple sum of `revenue - cost` per line, but then it loses its trap ("conservative additivity": it would be additive).

## 2. Two-hop dimension names for order-line metrics

**Blocks:** task 9 (catalog dimensions and `relations`), AC 5 and AC 13.

**Found:** Section 15 asks the fact models to carry the foreign keys they need, "so every dimension is one entity hop away and has these names" (`customer__region`, `customer__country`, `customer__segment`, `order__channel`, `product__category`). The demo does that: `order_items` has `customer_id`, `order_id` and `product_id`.

But because `order_items → order → customer` is also a valid join path, MetricFlow's `list_metrics()` / `list_dimensions(metric_names)` also return **`order__customer__region`, `order__customer__country` and `order__customer__segment`** for every metric on `order_items` (`revenue`, `units_sold`, `gross_margin`, `gross_margin_pct`, `discount_rate` and the helpers). They're the same attributes reached through another entity path. The spec says the catalog's dimensions are the "categorical dimensions reachable from at least one metric". Read literally, that gives eight categorical dimensions, not five, and `relations(["revenue"])` would include the two-hop names.

(MetricFlow also lists the time dimensions `order__order_date`, `order_item__order_date` and `inventory_snapshot__snapshot_date`. Section 3 already says only `metric_time` is exposed, so those aren't a question.)

**Options:**

- **A. Expose only dimensions with a single entity link** (MetricFlow's `entity_links` has length 1). The demo then has exactly the five names in section 15. On real projects this hides dimensions that are only reachable through two hops.
- **B. Expose every reachable categorical dimension, two-hop names included.** The catalog and `describe_metric` show near-duplicates (`customer__region` and `order__customer__region`), and section 15's list becomes "at least these".
- **C. Change the demo models so no two-hop path exists.** Not possible without losing a dimension: `orders` needs `customer_id` (for `customers` and `orders` by region), and `order_items` needs `order_id` (for `order__channel`).
