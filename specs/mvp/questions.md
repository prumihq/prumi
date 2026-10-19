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

**Decision (2026-10-17): A.** The catalog shows all twelve metrics; section 15 lists the three helpers. Whether a metric is public is the source's decision, and a Prumi hidden flag would be a curation layer. Spec updated.

## 2. Two-hop dimension names for order-line metrics

**Blocks:** task 9 (catalog dimensions and `relations`), AC 5 and AC 13.

**Found:** Section 15 asks the fact models to carry the foreign keys they need, "so every dimension is one entity hop away and has these names" (`customer__region`, `customer__country`, `customer__segment`, `order__channel`, `product__category`). The demo does that: `order_items` has `customer_id`, `order_id` and `product_id`.

But because `order_items → order → customer` is also a valid join path, MetricFlow's `list_metrics()` / `list_dimensions(metric_names)` also return **`order__customer__region`, `order__customer__country` and `order__customer__segment`** for every metric on `order_items` (`revenue`, `units_sold`, `gross_margin`, `gross_margin_pct`, `discount_rate` and the helpers). They're the same attributes reached through another entity path. The spec says the catalog's dimensions are the "categorical dimensions reachable from at least one metric". Read literally, that gives eight categorical dimensions, not five, and `relations(["revenue"])` would include the two-hop names.

(MetricFlow also lists the time dimensions `order__order_date`, `order_item__order_date` and `inventory_snapshot__snapshot_date`. Section 3 already says only `metric_time` is exposed, so those aren't a question.)

**Options:**

- **A. Expose only dimensions with a single entity link** (MetricFlow's `entity_links` has length 1). The demo then has exactly the five names in section 15. On real projects this hides dimensions that are only reachable through two hops.
- **B. Expose every reachable categorical dimension, two-hop names included.** The catalog and `describe_metric` show near-duplicates (`customer__region` and `order__customer__region`), and section 15's list becomes "at least these".
- **C. Change the demo models so no two-hop path exists.** Not possible without losing a dimension: `orders` needs `customer_id` (for `customers` and `orders` by region), and `order_items` needs `order_id` (for `order__channel`).

**Decision (2026-10-17): none of A–C as written: one name per attribute, the shortest path.** For each metric, group dimension names by final entity and dimension and keep the one with the fewest entity links (ties both stay). The demo shows exactly the five names in section 15, and on projects where an attribute is only reachable through two hops, the two-hop name is still shown. Spec section 4 updated. A filter that follows each block's own join path is recorded as Later.

## 3. The second-run dashboard can't pass rule 2 of section 19.13 with its own spec

**Blocks:** task 50 (bindings decide what a number may match) and everything after it, AC 52 ("every must-pass fixture still passes"). Task 48 is done: the first-run fixture has bindings, and the four new must-fail pages are in `packages/prumi-core/tests/fixtures/provenance/`.

**Found:** 19.13 rule 2 says a number must match a value of the block its nearest bound ancestor is bound to. The second-run page (`second_run.html` with the `spec.json` its agent wrote) binds `#detail-all` to `weekly_all` and `#detail` to `weekly_regions`. But its week-by-week table ends with a "Period" column that shows the `regions` block's values in those same elements: `regions.total` (the 12-week revenue, $2,742,409) in `#detail-all`, and each region's 12-week revenue in `#detail`. The numbers are correct and Prumi delivered them, but from another block, so rule 2 flags 9 of them at the default filters (more with average order value selected). No tuning fixes this without dropping rule 2: the rule is exact about which block a number may come from.

The section only says the first-run fixture gets bindings. With the new list form, the second-run fixture would pass with `"#detail": ["weekly_regions", "regions"]` and `"#detail-all": ["weekly_all", "regions"]`. No must-fail page gets through that way: the bindings only widen these two elements to a block they really show.

A related point for the browser check and the viewer bar (rule 5): the second-run page's tooltip (`#tip`, appended to `<body>`, outside every bound element) shows `trend` and `weekly_regions` values on hover. The server check never hovers, so it passes. But as published, every viewer who hovers a chart would flag the page and get the "This page shows numbers Prumi didn't deliver." bar, unless `#tip` is also bound (to `["trend", "weekly_regions"]`). The first-run page has the same kind of tooltip; its fixture now binds `#tip` to `weekly`, the only block it shows.

**Options:**

- **A. Edit the second-run fixture's spec**, as 19.13 does for the first-run one: list bindings for `#detail` and `#detail-all`, and bind `#tip`. The fixture then shows that a real agent page passes once its bindings say what each element shows, and the guide (task 54) teaches agents to write them. The second-run page as its agent wrote it no longer passes.
- **B. Move the second-run page to must-fail**, as written: rule 2 is meant to catch exactly this mixing, and an agent following the new guide would bind lists. The must-pass set then has one real agent page.
- **C. Loosen rule 2**: a number may match any block whose values the page shows elsewhere too, or any block of the dashboard. That brings back most of the coincidence problem rule 2 is there to fix (a `$229K` average matching some week).

I'd pick **A**: list bindings were added for tables like this one.

**Decision (2026-10-18): none of A–C. Remove the second-run dashboard from the fixtures.** The earlier test dashboards were written before these rules and aren't kept as they are; the rule stays exact. The first-run fixture keeps the bindings task 48 gave it. The page from the benchmark run (task 46), written with the new guide, becomes the real must-pass fixture. Spec 19.13 updated.

## 4. The semantic manifest has no project name

**Blocks:** the project name in the home page header (task 68, AC 72). The rest of task 68 doesn't depend on the answer.

**Found:** Section 20 puts "project name from the manifest" in the header. The adapter reads only `target/semantic_manifest.json` (`[source.dbt] semantic_manifest`), and that file has no project name: its top-level keys are `semantic_models`, `metrics`, `project_configuration` and `saved_queries`, and `project_configuration.metadata` is `null` in the demo (dbt-core 1.12.6). The project name is in dbt's full `target/manifest.json` (`metadata.project_name`, `prumi_demo` for the demo), which `dbt parse` writes into the same folder, and in `dbt_project.yml`, whose location Prumi isn't told.

Separately, the adapter protocol (section 3) has no way to tell core the project name or the connection type; core can't read `[source.dbt.connection]` itself without knowing dbt's config. Whatever the answer, the adapter needs one more member, e.g. `describe() -> SourceInfo(adapter, project, connection)` with `project: str | None`.

**Options:**

- **A. Read `metadata.project_name` from `manifest.json` next to the semantic manifest.** No new config. If the file is missing, unreadable or has no name, the header leaves the project name out (no startup error: the header is cosmetic).
- **B. A setting in `prumi.toml`** (`[source.dbt] project = "..."`), shown when present. Works for every adapter, but it's one more thing to configure and can drift from the project.
- **C. Drop the project name from the header** and show adapter, connection type and freshness only.

I'd pick **A**, with the protocol member above.

**Decision (2026-10-18): B, as `[source] project = "..."` in `prumi.toml`** (not under `[source.dbt]`): core reads it, so it works for every adapter and adapters never see it. Optional; without it the header leaves the name out. The demo config sets `project = "prumi_demo"`. The adapter gets `describe() -> SourceInfo(adapter, connection)`, without `project`. Spec sections 2 and 20 updated. (An earlier answer, C, was changed the same day.)

## 5. The fourth-run page can't be published once `R8.browser_locale` exists

**Blocks:** the AC 79 test after task 72 (AC 79 vs AC 80). Tasks 70 and 71 don't depend on the answer.

**Found:** The fourth-run page (`weekly-revenue-aov-regions.html`) formats the KPI's change with `new Intl.NumberFormat(undefined, {style: "percent", …})` (line 135) and writes it into `#kpi` (line 208), which is bound to `kpi`. In a `de-DE` render that text is `+5,3 %` instead of `+5.3%`, so rule 4 of section 21 fails it with `R8.browser_locale`. That's the rule working as intended: it's exactly the "pages that format with the browser's language themselves" case. But AC 79 says the page is "published under `en-US`", and from task 72 on, publishing validates it and refuses it.

(Before task 72, AC 79 holds: with the reader keeping its current separator readings, `+5,3%` in a `pt-BR` browser still reads as 5.3 and matches `_delta_pct`; only `US$ 47 mil` from `prumi.format` was flagged.)

**Options:**

- **A. The AC 79 test stores the page as it was published in the fourth run** (registry save, no validation), then opens it in a `pt-BR` browser. AC 79 then tests the viewer side only; the same page also serves as a must-fail example for `R8.browser_locale` (AC 80).
- **B. Edit the fixture** to use `prumi.locale` for its percent formatter, so it passes validation after task 72. It's then no longer the page as the agent wrote it.
- **C. Keep both:** the page as written (must fail `R8.browser_locale`) and an edited copy (must pass, and the AC 79 test publishes it).

I'd pick **C**: it shows both what went wrong in the fourth run and that the fix is a one-line change.

**Decision (2026-10-18): C.** The page as written is a must-fail for `R8.browser_locale`; a copy edited on that one line (to `prumi.locale`) is the AC 79 page. AC 79 updated.

**Also decided: the reader is strict, not lenient** (your note in task 71). It reads only the configured locale's separators; with `en-US`, `1.234,5` is no longer read as 1234.5. Accepting both conventions gave a made-up number two chances to match. Under C, the AC 79 page formats in `en-US` whatever the browser, so strict doesn't break it. Rule 3 of section 21 and AC 78 updated; task 71 needs a follow-up commit.
