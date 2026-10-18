# BI rules

Dashboards built by AI agents tend to make the same mistakes BI teams spent years learning to avoid: averaging averages, comparing a half month with a full one, totals that don't match their rows. Prumi checks a dashboard against ten rules, **R1 to R10**, before it's published and every time a block runs.

Issue codes start with the rule they belong to: `R6.others_requires_top_n` is a rule-6 problem. Codes that aren't about a BI rule use `spec.*`, `html.*`, `filter.*`, `query.failed` and `result.truncated`; the full list is in the [MVP spec, section 7](../specs/mvp/spec.md#7-validator-prumivalidate).

Every issue has a `code`, a `severity` (`error` blocks publishing; `warning` doesn't), a `path` into the spec (e.g. `data[1].others`), a `message` and a `hint` saying how to fix it.

| Rule | In one line | Status |
|---|---|---|
| [R1](#r1-non-additive-metrics-are-never-re-aggregated) | Non-additive metrics are never re-aggregated | Enforced |
| [R2](#r2-dimensions-must-be-valid-for-the-metric) | Dimensions must be valid for the metric | Enforced |
| [R3](#r3-partial-periods-are-flagged) | Partial periods are flagged | Enforced |
| [R4](#r4-period-comparisons-are-aligned) | Period comparisons are aligned | Enforced (standard calendar) |
| [R5](#r5-filter-scope-is-explicit) | Filter scope is explicit | Enforced |
| [R6](#r6-totals-and-others-are-separate-queries) | Totals and "Others" are separate queries | Enforced |
| [R7](#r7-only-source-metrics) | Only metrics from the source | Enforced |
| [R8](#r8-no-numbers-embedded-in-the-html) | No numbers embedded in the HTML | Basic checks |
| [R9](#r9-queries-run-as-the-viewer) | Queries run as the viewer | Planned |
| [R10](#r10-formats-and-units-come-from-the-metric-definition) | Formats and units come from the metric definition | Enforced |

## R1. Non-additive metrics are never re-aggregated

**Catches:** the average of daily averages shown as a monthly average; distinct customers per month summed into a yearly count.

Ratios, averages, distinct counts and balances can't be combined from finer rows. The source computes them at exactly the grain they're shown: for dbt, MetricFlow aggregates the numerator and denominator at the requested grain and divides them there.

**How Prumi enforces it:** by structure, not by a check.

- The engine computes only two things itself: "Others" for additive metrics (`total − sum of top N`, see R6) and comparison deltas (`value − previous`).
- The browser runtime has no aggregation helpers, and cross-filtering asks the server again instead of filtering rows in the browser.
- The catalog marks each metric `additive` or not. Only simple `sum`, `count` and `sum_boolean` metrics without a non-additive dimension are additive. When in doubt Prumi says non-additive, because a wrong "additive" gives wrong totals while a wrong "non-additive" only costs an extra query.

## R2. Dimensions must be valid for the metric

**Catches:** double counting from joins the data model doesn't declare, such as inventory split by customer region.

A metric can only be grouped or filtered by dimensions that the source reaches through declared relationships. For dbt, these are the semantic models' entities, and MetricFlow follows nothing else.

| Code | Severity | When |
|---|---|---|
| `R2.invalid_dimension` | error | A block's dimension isn't valid for all of its metrics |
| `R2.filter_dimension_invalid` | error | A block lists a filter on a dimension that isn't valid for all of its metrics |

## R3. Partial periods are flagged

**Catches:** "Sales dropped 60%" on the 5th of the month.

The period containing today (`as_of`) is the current, partial period. Windows default to complete periods (`{last: 12, grain: week}`), and a partial period only appears when asked for (`include_current`, `to_date`). Its rows are then marked `_partial: true`.

| Code | Severity | When |
|---|---|---|
| `R3.no_window` | error | A block has no time window. "All time" would silently include the current partial period |
| `R3.window_too_short` | error | An explicit date range contains no complete period |
| `R3.partial_in_comparison` | error | A comparison over a window that includes a partial period (use `to_date`, which aligns both sides) |
| `R3.window_snapped` | warning | An explicit date range was narrowed to whole periods |
| `R3.empty_window` | warning | A `to_date` window is empty (the first day of a period) |
| `R3.stale_data` | warning | The source has data only up to a day before the window ends, so the last period may be incomplete |

## R4. Period comparisons are aligned

**Catches:** a year-over-year comparison with a different number of days, or a weekly chart whose first and last weeks hold half a week of data.

Prumi computes every window as explicit dates and builds the comparison window itself: the same number of periods before (`previous_period`), or the same periods a year earlier (`previous_year`; weeks and days shift 364 days so weekdays line up). A block's time grain must divide its window exactly: months fit in quarters and years, weeks only fit in week windows.

| Code | Severity | When |
|---|---|---|
| `R4.grain_mismatch` | error | The block's time grain doesn't divide its window's periods (e.g. weeks in a month window) |
| `R4.compare_with_time_grain` | error | `compare` on a block grouped by time (not supported yet) |

Only the standard calendar is supported for now. Fiscal calendars, read from the source, are planned.

## R5. Filter scope is explicit

**Catches:** a KPI and a chart on the same page that disagree, because one follows the region filter and the other doesn't.

Every data block must list the filters that apply to it (`filters: [period, region]`, or `filters: []`). Filters a block doesn't list never affect it. The list is required by the spec format, so scope can't be left implicit.

| Code | Severity | When |
|---|---|---|
| `R5.multiple_windows` | error | A block has more than one time window (two time filters, or a time filter and a fixed `window`) |

## R6. Totals and "Others" are separate queries

**Catches:** a total that doesn't match its rows, or a "top 10 + Others" whose parts don't add up.

- A total is always its own source query, never a sum of rows.
- "Others" in a top N is `total − sum of top N` only for additive metrics. For non-additive metrics it's a separate source query for everything outside the top N, rows with no value (NULL) included.

| Code | Severity | When |
|---|---|---|
| `R6.top_n_shape` | error | `top_n` without exactly one dimension, with a time grain, or without a sort by a metric |
| `R6.others_requires_top_n` | error | `others` without `top_n` |
| `R6.total_requires_grouping` | error | `total` on a block that has nothing to total (no dimensions, no time grain) |

## R7. Only source metrics

**Catches:** an agent that can't find the metric it wants and quietly invents a formula.

A dashboard can only use metrics that exist in the source. If the right one is missing, the agent calls `request_metric`, which logs the request for the data team instead of guessing.

| Code | Severity | When |
|---|---|---|
| `R7.unknown_metric` | error | The metric isn't in the catalog. The message suggests close names; the hint points to `request_metric` |
| `R7.unknown_metric_request` | error | The spec lists a metric request id that doesn't exist |

## R8. No numbers embedded in the HTML

**Catches:** a dashboard that looks live but shows a frozen copy of the data pasted into the page.

The HTML is stored without data; every number arrives through the runtime. At publish, Prumi scans the HTML for literal datasets. The dashboard also runs in a sandboxed frame that can't make network calls, so its code can't fetch data from elsewhere or send it out.

| Code | Severity | When |
|---|---|---|
| `R8.embedded_numbers` | error | An inline script holds an array or object literal that looks like a dataset |
| `R8.numbers_in_text` | warning | Visible text with several data-like numbers (`1,234`, `12.5%`, `$3.4M`) |
| `R8.numbers_in_table` | warning | A table with several numeric cells |

**Limit:** this can't be fully enforced. An agent can still write numbers into an SVG or a sentence. The checks catch the common snapshot pattern, and a dashboard is only marked certified after a person has reviewed it.

## R9. Queries run as the viewer

**Catches:** a viewer seeing rows they're not allowed to see.

Prumi never decides who sees which rows. It runs each query with the viewer's own identity, so the warehouse applies its own access rules.

**Status:** planned. The MVP has a single local user and no login. The code already passes a viewer to every query, and each adapter declares whether it runs as each viewer or as a shared account.

## R10. Formats and units come from the metric definition

**Catches:** `0.25` on one dashboard and `25%` on another for the same metric, or USD and EUR on one axis.

Formats and units are read from the metric's definition in the source, and the runtime formats values with `prumi.format(value, metric)`. dbt's semantic layer has no format fields, so Prumi reads a small convention under `config.meta.prumi`; see [Preparing your dbt project](../packages/prumi-adapter-dbt/README.md#preparing-your-dbt-project).

| Code | Severity | When |
|---|---|---|
| `R10.no_format` | warning | A metric has no format declared (or an invalid one); it's shown as a plain number |
| `R10.mixed_units` | warning | One block shows metrics with different units |
