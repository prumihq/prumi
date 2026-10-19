# Writing a Prumi dashboard

A dashboard is two things you write side by side: a **spec** (JSON) that says which data each block shows, and an **HTML page** that draws it. Prumi runs every query; the page only receives results. Read this once, then write both.

Prumi never sends company data to your agent: `query` shows the shape of the data, and the numbers appear only in the dashboard. What you see are metric definitions, the shape of results, issue codes and element paths.

## How to work

1. `find_metrics`, then `describe_metric` for each metric you'll use. Use only catalog metrics and the dimensions `describe_metric` lists; if one is missing, call `request_metric`.
2. `query` to learn the shape of the data: how many rows, distinct values and empties per column, the shortest and longest label, and for each metric its sign, magnitude (integer digits: 4 to 5 means 1,000 to 99,999) and the decimals its format shows. Design axes, widths and label space from that; it returns no values.
3. Write the spec and the HTML below.
4. `validate_spec` with both, and fix every error. Send the HTML straight to `validate_spec`; don't write it to a local file first. Validation renders the page in a browser with the blocks' data and checks every number it shows (see "Every number comes from Prumi"). The result always has a `candidate_id`: publish it when `ok`; when it isn't, fix it with `validate_spec(base=candidate_id, html_edits=[...])` instead of sending the page again. A runtime error gives the line and column in your HTML and a short stack.
   - Pass `check_states` to check filter states viewers will pick, not just the defaults: up to 5, like `[{"filters": {"region": ["North America"]}}, {"filters": {"period": {"last": 52, "grain": "week"}}}]`. Each is rendered and checked the same way; its issues start with "With the filters …". The states stay with the candidate and are checked again when it's published.
   - **Read the layout warnings** after each validation. The check measures its render (desktop, 1280×800) and reports `layout.text_overlap`, `layout.text_clipped`, `layout.too_wide`, `layout.small_text` (under 10 px) and `layout.empty_block` (a bound element that renders nothing), each with element paths and boxes (`x, y, width×height`). Pass `layout_devices: ["phone"]` to measure a phone render (390×844) too. Fix them with `html_edits` on the candidate. They're warnings: they don't block publishing, but each is something a viewer will see.
5. `publish_dashboard` with that `candidate_id` (don't send the spec and HTML again). Give the user the URL; the dashboard starts as an uncertified draft.
6. If you can open a browser, take one screenshot of the draft at desktop width; don't read the page's text. Either way, ask the user to look at the draft link for what a measurement can't judge: whether the page reads well, the colors, the order of things.

To change a dashboard later, see "Changing a dashboard" at the end.

## The spec

```json
{
  "spec_version": 0,
  "dashboard": {"id": "revenue-by-region", "title": "Revenue by region"},
  "filters": [ ... ],
  "data": [ ... ],
  "interactions": [ ... ],
  "metric_requests": [],
  "presentation": {"bindings": {"#element-id": "block-id"}}
}
```

- `dashboard.id`: lowercase letters, digits and hyphens. Publishing the same id again makes a new version.
- **Filters** are shared controls. A time filter: `{"id": "period", "type": "time", "default": <window>}`. A dimension filter: `{"id": "region", "type": "dimension", "dimension": "customer__region", "default": []}`; its value is a list of values, `[]` means all, `null` in the list matches missing values.
- **Windows** (time filter defaults, a block's fixed `window`, and `query`):
  - `{"last": 12, "grain": "week"}`: the 12 complete weeks before the current one. Prefer this form.
  - `{"last": 3, "grain": "month", "include_current": true}`: plus the current, partial month (rows get `_partial: true`).
  - `{"to_date": "month"}`: the current month up to yesterday.
  - `{"start": "2026-01-01", "end": "2026-06-30", "grain": "month"}`: dates, snapped inward to whole periods.
  - Grains: `day`, `week` (Monday to Sunday), `month`, `quarter`, `year`.
- **Blocks** (`data`): each is one query.
  - `id`, `metrics` (1–10), `filters` (**required**: the ids of the filters that apply to this block; `[]` for none), and one window: a time filter in `filters`, or a fixed `window`.
  - Optional: `title`, `dimensions` (0–3), `time_grain` (one series point per period), `sort: {"by": <metric, dimension or "metric_time">, "descending": true}`, `top_n` (1–100), `others`, `total`, `compare` (`previous_period` or `previous_year`).
  - `top_n` needs exactly one dimension, no `time_grain`, and `sort` by one of the block's metrics. `others` needs `top_n`. `total` needs dimensions or a `time_grain`. `compare` only works without `time_grain`.
  - `total` on a block with a `time_grain` is one grand total for the whole window, not one per period. For a total per period, add a block with the same `time_grain` and no dimensions.
  - A block returns at most 5,000 rows (`truncated: true` beyond that).
  - A `time_grain` must divide the window's periods: the same grain, `day`, `month` in a quarter or year, `quarter` in a year. Weeks only fit week windows.
- **Interactions**: `{"from": "regions", "on": "click", "sets_filter": "region"}`. A click on a row of `regions` sets the dimension filter `region` to that row's value; clicking it again clears it. The block must have the filter's dimension.
- **Bindings** (`presentation.bindings`): `"#element-id": "block-id"`, or a list of blocks when the element shows several (`"#table": ["regions", "regions_total"]`). **Every element that shows numbers must be bound** (or sit inside a bound element) to the blocks whose values it shows: a number is checked against the blocks of its nearest bound ancestor. The element must be in the HTML (an id in the page, not one a script creates).

## The page

Your HTML runs in a sandboxed frame. It **can't** call any server (no `fetch`, `XMLHttpRequest` or `WebSocket`, no images from other hosts) and can't see the page around it. Scripts, stylesheets and fonts may come from `cdn.jsdelivr.net`, `cdnjs.cloudflare.com` or `unpkg.com` (fonts also from Google Fonts), pinned to an exact version (`chart.js@4.4.1`). Prumi adds its runtime to the page; you don't load it.

### Every number comes from Prumi

**Every number the page shows must be a value Prumi delivered to the block bound there**: a metric value from that block's result (rows, `total`, `others`, `_previous`, `_delta`, `_delta_pct`), as `prumi.format` shows it. Validation renders the page with the data, reads every number it shows (text, SVG text, `title` tooltips), then hovers chart marks and reads the tooltips they open; viewers' browsers do the same for other filter states. Errors block publishing:

- `R8.unbound_number`: a number outside every bound element. Bind the element to the block it shows.
- `R8.unverified_number`: a number its binding's blocks didn't deliver: a sum, average or share computed in JavaScript, a value from another block (bind a list of blocks), a number typed in from anywhere else. Ask for `total`, `others` or `compare` in the spec instead.
- `R8.copied_number`: a delivered value written into the HTML source or a script, even if it's right today. It goes stale; show it from `prumi.on`.

The rules:

- A shown number matches a delivered value at the precision shown, also scaled (`$2.7M`, `31%`).
- **Not data, fine anywhere** (bound or not), exactly these patterns:
  - dates: `2026-10-14`, `2026-10`, `2026/10/14`, `10/14/2026`, `14.10.2026`, `Oct 14`, `Oct 14, 2026`, `14 October 2026`, `Oct 2026`, `Oct '26`, `Q3`, `Q3 2026`, `2026 Q3`, `FY2026`, `W42`, `Week 42`, and times (`06:30`, `6:30 pm`);
  - years: whole numbers from 1900 to 2199 written without separators (`2026`);
  - period counts: a whole number followed by `day`, `week`, `wk`, `month`, `mo`, `quarter`, `qtr`, `year` or `yr` (singular or plural), not after a currency symbol: "Last 26 weeks", "12 wk";
  - numbers from the spec or result metadata: anything written in the spec (`last`, `top_n`) or in a label, a block's row count, numbers inside dimension values ("Store 12");
  - axis ticks (below).
- **Tooltips** are checked too, on hover. Render them inside the bound chart element, or give them an element in the HTML (`<div id="tip" hidden></div>`) and bind it to the blocks they show (`"#tip": ["trend", "weekly_regions"]`).
- **Layout math is fine**: scales, widths, positions, color ranges, and ordering or sorting by a value computed in JavaScript, as long as the computed value isn't shown. Compute anything you need to *draw* or *arrange*; *show* only delivered values.
- **Axis ticks** aren't data. Inside an `<svg>`, or an element marked `data-prumi-role="axis"` (an SVG `<g>`, a `<div>`), three or more evenly spaced numbers are ticks at any precision: `$280, $286, $292` is a fine axis. A single round number (at most two significant digits: `0`, `50K`, `2.5M`, `20%`) is a tick only in an axis-marked element. Outside both, nothing is a tick.
- **Nothing the check can't read**: no `<canvas>` (`html.canvas`), no `<iframe>` (`html.iframe`), no closed shadow roots (`html.closed_shadow_root`) and no images (`html.image`: `<img>`, SVG `<image>`, CSS background images; the frame blocks them too). Draw charts with SVG or HTML elements (chart libraries that render to SVG are fine), and icons as inline SVG.
- If a viewer's browser later finds a number Prumi didn't deliver (new data, another filter state), the viewer sees "This page shows numbers Prumi didn't deliver." and the dashboard is marked `needs_attention` in `list_dashboards` and `get_dashboard` until it's fixed.
- A page that throws while rendering (`html.runtime_error`) or never stops changing (`html.render_timeout`, after 10 s) fails validation too. In the browser, a callback that throws, or a block that fails without a `prumi.onError` handler, shows "This part of the dashboard couldn't load." in the block's bound elements.

### Theme

Prumi loads a default theme before your styles: CSS variables on `:root`, light by default and dark when the viewer's system is in dark mode. Use them to match Prumi's shell in both modes, or ignore them.

`--prumi-bg`, `--prumi-surface`, `--prumi-text`, `--prumi-text-muted`, `--prumi-border`, `--prumi-accent`, `--prumi-positive`, `--prumi-negative`, `--prumi-series-1` … `--prumi-series-8` (chart colors), `--prumi-font`, `--prumi-font-mono`, `--prumi-radius`.

### `window.prumi`

| Call | Does | Returns |
|---|---|---|
| `prumi.on(blockId, callback)` | Calls `callback(result)` with the block's result when the page loads, then again after each change to a filter the block lists. | nothing |
| `prumi.onError(blockId, callback)` | Calls `callback(issue)` when the block can't be loaded (`issue`: `{code, severity, path, message, hint}`). | nothing |
| `prumi.setFilter(filterId, value)` | Sets a filter: a list of values (`[]` for all) for a dimension filter, a window object for a time filter. Blocks that list it reload. | nothing |
| `prumi.getFilter(filterId)` | The filter's current value. | value |
| `prumi.onFilters(callback)` | Calls `callback(filters)` with all current values (`{filterId: value}`) on load and on every change. | nothing |
| `prumi.filterValues(filterId)` | The distinct values of a dimension filter, for building a picker. | `Promise<values[]>` |
| `prumi.click(blockId, row)` | Applies the interactions declared `from` that block, using `row` (an item of `result.rows`). | nothing |
| `prumi.format(value, metric, options?)` | Formats a value for display. `metric` is a metric name (`"revenue"`): the type and unit always come from the metric's definition. `options` only change the presentation: `{decimals: 0}`, `{compact: true}` (`$80K`, `$2.7M`). `null` gives `"–"`. Anything other than a metric name (a format object, a typo) formats as a plain number and warns in the browser console. | string |
| `prumi.label(name)` | A metric's or dimension's label from the catalog (`"customer__region"` → `"Region"`). | string |
| `prumi.locale` | The server's display locale (`"en-US"`, `"pt-BR"`), a string. `prumi.format` uses it; pass it to your own `Intl` calls. | string |

### The result object

What `prumi.on` callbacks receive:

```json
{
  "block": "regions",
  "columns": [
    {"name": "customer__region", "kind": "dimension", "label": "Region"},
    {"name": "revenue", "kind": "metric", "label": "Revenue",
     "format": {"type": "currency", "unit": "USD", "decimals": 2}, "additive": true}
  ],
  "rows": [{"customer__region": "North America", "revenue": 1234.5}],
  "others": {"revenue": 210.0},
  "total": {"revenue": 4321.0},
  "window": {"start": "2026-07-20", "end": "2026-10-11", "grain": "week", "partial": false},
  "comparison_window": null,
  "freshness": {"revenue": "2026-10-14"},
  "warnings": [],
  "truncated": false
}
```

- `rows` are objects keyed by column name. Blocks with a `time_grain` have a `metric_time` column: the ISO date of each period's start. Rows of the current, partial period have `_partial: true`.
- Blocks with `compare` add `_previous`, `_delta` and `_delta_pct` to each row, each an object keyed by metric (`row._delta_pct.revenue` is a fraction: 0.12 is +12%; `null` when there's nothing to compare with).
- `others` (with `top_n` and `others`) and `total` (with `total`) are objects keyed by metric, or `null` when not asked for or when every group is already in the top N. They carry no comparison values in this release: `_previous`, `_delta` and `_delta_pct` are on rows only. For an overall change, add a block without dimensions that has `compare`.
- An empty result has `rows: []`, for example a month to date on its first day. Draw an empty state.
- Format metric values (and `_previous`, `_delta`, `total`, `others`) with `prumi.format(value, "<metric>")`. Percent metrics are fractions (0.25 shows as 25%). `_delta_pct` is a fraction too, whatever the metric's unit: show it as a percent with `new Intl.NumberFormat(undefined, {style: "percent", maximumFractionDigits: 1}).format(value)`.

## Example

Replace `revenue` and `customer__region` with metrics and dimensions from `find_metrics` and `describe_metric`.

### Example spec

```json
{
  "spec_version": 0,
  "dashboard": {"id": "revenue-by-region", "title": "Revenue by region"},
  "filters": [
    {"id": "period", "type": "time", "default": {"last": 12, "grain": "week"}},
    {"id": "region", "type": "dimension", "dimension": "customer__region", "default": []}
  ],
  "data": [
    {"id": "kpi", "metrics": ["revenue"], "filters": ["period", "region"], "compare": "previous_year"},
    {
      "id": "regions",
      "metrics": ["revenue"],
      "dimensions": ["customer__region"],
      "filters": ["period"],
      "sort": {"by": "revenue", "descending": true},
      "top_n": 5,
      "others": true,
      "total": true
    },
    {"id": "trend", "metrics": ["revenue"], "time_grain": "week", "filters": ["period", "region"]}
  ],
  "interactions": [{"from": "regions", "on": "click", "sets_filter": "region"}],
  "presentation": {"bindings": {"#kpi": "kpi", "#regions": "regions", "#others": "regions", "#trend": "trend"}}
}
```

### Example HTML

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Revenue by region</title>
  <style>
    body { font-family: var(--prumi-font); background: var(--prumi-bg); color: var(--prumi-text); margin: 24px; }
    #kpi strong { font-size: 32px; }
    #regions button { display: block; margin: 4px 0; }
  </style>
</head>
<body>
  <h1>Revenue by region</h1>
  <p id="kpi"><strong id="revenue"></strong> <span id="change"></span></p>
  <div id="regions"></div>
  <p id="others"></p>
  <ol id="trend"></ol>
  <script>
    // _delta_pct is a fraction whatever the metric's unit, so it's shown as a percent.
    const pct = new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 1 });

    prumi.on("kpi", (result) => {
      const row = result.rows[0];
      document.getElementById("revenue").textContent = row ? prumi.format(row.revenue, "revenue") : "–";
      const change = row && row._delta_pct.revenue;
      document.getElementById("change").textContent =
        change == null ? "" : `${pct.format(change)} vs last year`;
    });

    prumi.on("regions", (result) => {
      document.getElementById("regions").replaceChildren(...result.rows.map((row) => {
        const button = document.createElement("button");
        button.textContent = `${row.customer__region ?? "(none)"}: ${prumi.format(row.revenue, "revenue")}`;
        button.onclick = () => prumi.click("regions", row);
        return button;
      }));
      document.getElementById("others").textContent = result.others
        ? `Others: ${prumi.format(result.others.revenue, "revenue")} · Total: ${prumi.format(result.total.revenue, "revenue")}`
        : "";
    });

    prumi.on("trend", (result) => {
      document.getElementById("trend").replaceChildren(...result.rows.map((row) => {
        const item = document.createElement("li");
        item.textContent = `${row.metric_time}: ${prumi.format(row.revenue, "revenue")}${row._partial ? " (so far)" : ""}`;
        return item;
      }));
    });

    for (const block of ["kpi", "regions", "trend"]) {
      prumi.onError(block, (issue) => console.error(block, issue.message));
    }
  </script>
</body>
</html>
```

## Changing a dashboard

1. `list_dashboards` lists the published dashboards (`needs_attention`: viewers' browsers found numbers Prumi didn't deliver); `get_dashboard(id)` gives the current version's spec and HTML, and its `checks`: what viewers' browsers flagged, per filter state.
2. `validate_spec(base=..., html_edits=[...], spec=...)` validates a change without resending the page. `base` is a `candidate_id` (also one whose validation failed), a dashboard id for its current version (`"sales-weekly"`), or `{"dashboard_id": "...", "version": 3}`; `html_edits` are `{"old": "...", "new": "..."}` replacements applied in order, and each `old` must occur exactly once in the HTML at that point (`edit.no_match`, `edit.ambiguous`); `spec` is a full new spec (default: the base's). The result is validated like any content and has its own `candidate_id`. Edits of a candidate keep its `check_states` unless you pass new ones.
3. `publish_dashboard` with that `candidate_id` makes a new version, a draft again.

## The spec's JSON Schema

<!-- schema:start -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Prumi dashboard spec, format version 0",
  "$defs": {
    "Block": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "pattern": "^[a-z0-9][a-z0-9_-]{0,62}$",
          "title": "Id",
          "type": "string"
        },
        "title": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Title"
        },
        "metrics": {
          "items": {
            "type": "string"
          },
          "maxItems": 10,
          "minItems": 1,
          "title": "Metrics",
          "type": "array"
        },
        "dimensions": {
          "default": [],
          "items": {
            "type": "string"
          },
          "maxItems": 3,
          "title": "Dimensions",
          "type": "array"
        },
        "time_grain": {
          "anyOf": [
            {
              "enum": [
                "day",
                "week",
                "month",
                "quarter",
                "year"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Time Grain"
        },
        "filters": {
          "items": {
            "type": "string"
          },
          "title": "Filters",
          "type": "array"
        },
        "window": {
          "anyOf": [
            {
              "oneOf": [
                {
                  "$ref": "#/$defs/LastWindow"
                },
                {
                  "$ref": "#/$defs/ToDateWindow"
                },
                {
                  "$ref": "#/$defs/RangeWindow"
                }
              ]
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Window"
        },
        "compare": {
          "anyOf": [
            {
              "enum": [
                "previous_period",
                "previous_year"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Compare"
        },
        "sort": {
          "anyOf": [
            {
              "$ref": "#/$defs/Sort"
            },
            {
              "type": "null"
            }
          ],
          "default": null
        },
        "top_n": {
          "anyOf": [
            {
              "maximum": 100,
              "minimum": 1,
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Top N"
        },
        "others": {
          "default": false,
          "title": "Others",
          "type": "boolean"
        },
        "total": {
          "default": false,
          "title": "Total",
          "type": "boolean"
        }
      },
      "required": [
        "id",
        "metrics",
        "filters"
      ],
      "title": "Block",
      "type": "object"
    },
    "DashboardInfo": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "pattern": "^[a-z0-9][a-z0-9-]{0,62}$",
          "title": "Id",
          "type": "string"
        },
        "title": {
          "maxLength": 200,
          "minLength": 1,
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "id",
        "title"
      ],
      "title": "DashboardInfo",
      "type": "object"
    },
    "DimensionFilter": {
      "additionalProperties": false,
      "description": "An `in` filter on one dimension. `[]` means no filter; `null` matches NULL.",
      "properties": {
        "id": {
          "pattern": "^[a-z0-9][a-z0-9_-]{0,62}$",
          "title": "Id",
          "type": "string"
        },
        "type": {
          "const": "dimension",
          "title": "Type",
          "type": "string"
        },
        "dimension": {
          "title": "Dimension",
          "type": "string"
        },
        "default": {
          "type": "array",
          "items": {
            "anyOf": [
              {
                "type": "string"
              },
              {
                "type": "integer"
              },
              {
                "type": "number"
              },
              {
                "type": "boolean"
              },
              {
                "type": "null"
              }
            ]
          },
          "default": [],
          "title": "Default"
        }
      },
      "required": [
        "id",
        "type",
        "dimension"
      ],
      "title": "DimensionFilter",
      "type": "object"
    },
    "Interaction": {
      "additionalProperties": false,
      "properties": {
        "from": {
          "title": "From",
          "type": "string"
        },
        "on": {
          "const": "click",
          "title": "On",
          "type": "string"
        },
        "sets_filter": {
          "title": "Sets Filter",
          "type": "string"
        }
      },
      "required": [
        "from",
        "on",
        "sets_filter"
      ],
      "title": "Interaction",
      "type": "object"
    },
    "LastWindow": {
      "additionalProperties": false,
      "description": "The N complete periods of `grain` before the current one (plus it, if asked).",
      "properties": {
        "last": {
          "maximum": 400,
          "minimum": 1,
          "title": "Last",
          "type": "integer"
        },
        "grain": {
          "enum": [
            "day",
            "week",
            "month",
            "quarter",
            "year"
          ],
          "title": "Grain",
          "type": "string"
        },
        "include_current": {
          "default": false,
          "title": "Include Current",
          "type": "boolean"
        }
      },
      "required": [
        "last",
        "grain"
      ],
      "title": "LastWindow",
      "type": "object"
    },
    "Presentation": {
      "additionalProperties": false,
      "properties": {
        "bindings": {
          "additionalProperties": {
            "anyOf": [
              {
                "type": "string"
              },
              {
                "items": {
                  "type": "string"
                },
                "minItems": 1,
                "type": "array"
              }
            ]
          },
          "default": {},
          "title": "Bindings",
          "type": "object"
        }
      },
      "title": "Presentation",
      "type": "object"
    },
    "RangeWindow": {
      "additionalProperties": false,
      "description": "Inclusive dates, snapped inward to whole periods of `grain`.",
      "properties": {
        "start": {
          "format": "date",
          "title": "Start",
          "type": "string"
        },
        "end": {
          "format": "date",
          "title": "End",
          "type": "string"
        },
        "grain": {
          "anyOf": [
            {
              "enum": [
                "day",
                "week",
                "month",
                "quarter",
                "year"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Grain"
        },
        "include_current": {
          "default": false,
          "title": "Include Current",
          "type": "boolean"
        }
      },
      "required": [
        "start",
        "end"
      ],
      "title": "RangeWindow",
      "type": "object"
    },
    "Sort": {
      "additionalProperties": false,
      "properties": {
        "by": {
          "title": "By",
          "type": "string"
        },
        "descending": {
          "default": false,
          "title": "Descending",
          "type": "boolean"
        }
      },
      "required": [
        "by"
      ],
      "title": "Sort",
      "type": "object"
    },
    "TimeFilter": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "pattern": "^[a-z0-9][a-z0-9_-]{0,62}$",
          "title": "Id",
          "type": "string"
        },
        "type": {
          "const": "time",
          "title": "Type",
          "type": "string"
        },
        "default": {
          "oneOf": [
            {
              "$ref": "#/$defs/LastWindow"
            },
            {
              "$ref": "#/$defs/ToDateWindow"
            },
            {
              "$ref": "#/$defs/RangeWindow"
            }
          ],
          "title": "Default"
        },
        "calendar": {
          "default": "standard",
          "title": "Calendar",
          "type": "string"
        }
      },
      "required": [
        "id",
        "type",
        "default"
      ],
      "title": "TimeFilter",
      "type": "object"
    },
    "ToDateWindow": {
      "additionalProperties": false,
      "description": "From the start of the current period of `to_date` to the day before as_of.",
      "properties": {
        "to_date": {
          "enum": [
            "day",
            "week",
            "month",
            "quarter",
            "year"
          ],
          "title": "To Date",
          "type": "string"
        }
      },
      "required": [
        "to_date"
      ],
      "title": "ToDateWindow",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "properties": {
    "spec_version": {
      "const": 0,
      "title": "Spec Version",
      "type": "integer"
    },
    "dashboard": {
      "$ref": "#/$defs/DashboardInfo"
    },
    "filters": {
      "default": [],
      "items": {
        "discriminator": {
          "mapping": {
            "dimension": "#/$defs/DimensionFilter",
            "time": "#/$defs/TimeFilter"
          },
          "propertyName": "type"
        },
        "oneOf": [
          {
            "$ref": "#/$defs/TimeFilter"
          },
          {
            "$ref": "#/$defs/DimensionFilter"
          }
        ]
      },
      "title": "Filters",
      "type": "array"
    },
    "data": {
      "items": {
        "$ref": "#/$defs/Block"
      },
      "title": "Data",
      "type": "array"
    },
    "interactions": {
      "default": [],
      "items": {
        "$ref": "#/$defs/Interaction"
      },
      "title": "Interactions",
      "type": "array"
    },
    "metric_requests": {
      "default": [],
      "items": {
        "type": "integer"
      },
      "title": "Metric Requests",
      "type": "array"
    },
    "presentation": {
      "$ref": "#/$defs/Presentation",
      "default": {
        "bindings": {}
      }
    }
  },
  "required": [
    "spec_version",
    "dashboard",
    "data"
  ],
  "type": "object"
}
```
<!-- schema:end -->
