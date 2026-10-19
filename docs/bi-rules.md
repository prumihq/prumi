# BI rules

Dashboards built by AI agents tend to make the same mistakes BI teams spent years learning to avoid: averaging averages, comparing a half month with a full one, totals that don't match their rows. Prumi checks a dashboard against ten rules, **R1 to R10**, before it's published and every time a block runs.

Issue codes start with the rule they belong to: `R6.others_requires_top_n` is a rule-6 problem. Codes that aren't about a BI rule use `spec.*`, `html.*`, `filter.*`, `publish.*`, `validate.*`, `edit.*`, `dashboard.*`, `layout.*` (warnings about the rendered page's layout), `query.failed` and `result.truncated`; the full list is in the [MVP spec, sections 7, 18 and 19](../specs/mvp/spec.md#7-validator-prumivalidate).

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
| [R8](#r8-every-number-shown-is-a-value-prumi-delivered) | Every number shown is a value Prumi delivered | Enforced (checked in a browser) |
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

## R8. Every number shown is a value Prumi delivered

**Catches:** a dashboard that looks live but shows numbers the source never computed: a frozen copy of the data pasted into the page, a value copied from an earlier result (even one that's right today), a total summed or an average computed in JavaScript.

The HTML is stored without data; every number arrives through the runtime. It doesn't matter how a wrong number got onto the page: if Prumi didn't deliver it, it's flagged.

**Every number belongs to a bound block.** Each element that shows numbers must be matched by a `presentation.bindings` selector (or sit inside one), and its numbers must be values of **that binding's blocks**; the nearest bound ancestor wins. A binding names one block or a list (`"#table": ["regions", "regions_total"]`). Matching against every value on the page isn't enough: a page shows hundreds of values, and a number computed in JavaScript often equals one of them at the precision shown (a weekly average shown as `$229K` equals some week's revenue).

**What counts as delivered:** every metric value in the bound blocks' results (rows, `total`, `others`, `_previous`, `_delta`, `_delta_pct`). A number on screen matches when such a value, rounded to the precision shown and scaled for `%` or the locale's compact words (`K`/`M`/`B` in en-US, `mil`/`mi`/`bi` in pt-BR), gives the same number; currency symbols and signs are ignored. Numbers are read strictly in the server's locale (`[server] locale`): its own group and decimal separators only, taken from `Intl`, so in en-US `1.234` is 1.234 and `1.234,5` matches nothing. `$2.7M` matches `2742409.17`; `$228,534` doesn't match anything if only `2742409.17` was delivered.

**Allowed anywhere, bound or not:** dates and years in common formats, counts of periods ("Last 4 weeks"), numbers written in the spec or in result metadata (window lengths, `top_n`, row counts, numbers in dimension values), and axis ticks: inside an `<svg>` or an element marked `data-prumi-role="axis"`, any number that's part of a run of three or more evenly spaced values (at any precision: `$280, $286, $292`), and, in an axis-marked element, a single round number with at most two significant digits (`0`, `50K`, `2.5M`, `20%`). Outside both, nothing is a tick.

**One locale for every viewer.** `prumi.format` formats in the server's locale and pages get it as `prumi.locale`, so every viewer sees the same text whatever their browser's language. At validation, the default state is rendered once more in another browser language (`de-DE`, or `en-US` for a `de-*` server); a bound element whose text changes formats with the browser's language (`toLocaleString()`, `Intl.*(undefined, …)`), and the page fails.

**Not in the source:** a delivered value written into the HTML source (3+ significant digits in text, 4+ in a script) was copied there, so it's right only until the data changes. Attributes such as SVG coordinates aren't read, and round layout constants (`1200`) have too few digits.

**What's read:** visible text, i.e. text outside `<script>` and `<style>`, SVG `<text>`, `title` attributes and open shadow roots, including text that appears later, such as tooltips. What the check can't read isn't allowed: `<canvas>`, `<iframe>`, closed shadow roots and images (`<img>`, SVG `<image>`, CSS background images); the frame's CSP blocks images too (`img-src 'none'`).

The number check runs in two places:

- **On the server, at validation (the guarantee).** When the static checks and the dry run pass, Prumi renders the page in headless Chromium with the dry run's results, waits until it settles (no DOM changes for 300 ms, at most 10 s) and reads every number from an isolated world: page code shares the DOM with the check but none of its JavaScript, so it can't switch the check off or feed it values. It also scans the stored source for copied values, then hovers up to 20 chart marks (elements with hover listeners under bound elements, and SVG shapes in them) and reads the tooltips they open; that adds about a second. Errors block publishing, and `prumi certify` runs the check again. It checks the filters' default values, and the filter states the agent passes as `check_states` (up to 5, also checked again at publish), with that day's data.
- **In viewers' browsers, every time the page is viewed.** The runtime runs the same check after each render and reports what it found for that filter state; the registry keeps the latest report per version and filter state. When a report flags numbers, that viewer sees a bar, "This page shows numbers Prumi didn't deliver.", with the list on click, and the current version is marked `needs_attention` (status API, `list_dashboards`, `get_dashboard`) until a new version is published or a later report for that filter state is clean. Publishing can't catch what only appears after it: new data, or a filter state the server didn't render. Page code could interfere with this check, so it's not a guarantee, but `get_dashboard` shows the reports and `prumi certify` refuses a version with a flagged report.

The static checks still run first, and the frame can't make network calls, so page code can't fetch data from elsewhere or send it out.

| Code | Severity | When |
|---|---|---|
| `R8.unbound_number` | error | The rendered page shows a number outside every bound element, or a tooltip does on hover. The issue gives the element's path and how many numbers, never the numbers |
| `R8.unverified_number` | error | A number in a bound element isn't a value its binding's blocks delivered. The issue gives the element's path, how many and the blocks; at most 20 numbers in all |
| `R8.browser_locale` | error | A bound element's text changes with the browser's language. The issue gives the element paths, never the text |
| `R8.copied_number` | error | The HTML source holds a delivered value (in text with 3+ significant digits, in a script with 4+). The issue gives the line, not the value |
| `R8.embedded_numbers` | error | An inline script holds an array or object literal that looks like a dataset |
| `R8.numbers_in_text` | warning | Visible text with several data-like numbers (`1,234`, `12.5%`, `$3.4M`) |
| `R8.numbers_in_table` | warning | A table with several numeric cells |
| `html.canvas` | error | The page uses `<canvas>`, whose text can't be checked |
| `html.iframe` | error | The page has an `<iframe>` (or `<frame>`), which the check can't read inside |
| `html.closed_shadow_root` | error | The page makes a closed shadow root (`attachShadow({mode: "closed"})`, `<template shadowrootmode="closed">`) |
| `html.image` | error | The page has an image: `<img>`, SVG `<image>`, `<picture>`, `<input type="image">` or a CSS background image |
| `html.runtime_error` | error | The page, or a `prumi.on` callback, threw an error while rendering. The issue gives the line and column in the submitted HTML and up to 5 lines of the stack |
| `html.render_timeout` | error | The page didn't settle within 10 s, so its numbers couldn't be checked |
| `html.network_call` | warning | A script calls `fetch(`, `XMLHttpRequest`, `WebSocket`, `EventSource` or `sendBeacon`, which the frame blocks |
| `html.blocked_request` | warning | While rendering, the page tried a network call and the frame blocked it |

**What the check can't see: visual encoding.** It reads the numbers a page shows, not the shapes it draws. A bar whose height was computed from the wrong value, a line plotted against the wrong axis, or a color scale that misleads all pass. Whether the picture matches the numbers is what a reviewer still has to judge before certifying.

**Other limits, by design:**
- A made-up number that completes a run of three evenly spaced values in a chart passes as an axis tick: next to ticks at `$200` and `$250`, a typed `$300` looks like the next one.
- A low-precision number can still equal some value of its own block by chance at the precision shown; binding numbers to their blocks narrows this, it doesn't remove it.
- A value typed into the page in pieces the source scan can't recognize (`"2,742," + "409"` in a script) is right at publish, so it passes then; once the data changes, viewers' browsers flag it.

## R9. Queries run as the viewer

**Catches:** a viewer seeing rows they're not allowed to see.

Prumi never decides who sees which rows. It runs each query with the viewer's own identity, so the warehouse applies its own access rules.

**Status:** planned. The MVP has a single local user and no login. The code already passes a viewer to every query, and each adapter declares whether it runs as each viewer or as a shared account.

## R10. Formats and units come from the metric definition

**Catches:** `0.25` on one dashboard and `25%` on another for the same metric, or USD and EUR on one axis.

Formats and units are read from the metric's definition in the source, and the runtime formats values with `prumi.format(value, metric)`. Pages can only adjust the presentation (`{decimals}`, `{compact: true}`); passing a unit of their own gives a plain number and a console warning. dbt's semantic layer has no format fields, so Prumi reads a small convention under `config.meta.prumi`; see [Preparing your dbt project](../packages/prumi-adapter-dbt/README.md#preparing-your-dbt-project).

| Code | Severity | When |
|---|---|---|
| `R10.no_format` | warning | A metric has no format declared (or an invalid one); it's shown as a plain number |
| `R10.mixed_units` | warning | One block shows metrics with different units |
