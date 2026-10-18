# Writing a Prumi dashboard

A dashboard is two things you write side by side: a **spec** (JSON) that says which data each block shows, and an **HTML page** that draws it. Prumi runs every query; the page only receives results. Read this once, then write both.

## How to work

1. `find_metrics`, then `describe_metric` for each metric you'll use. Use only catalog metrics and the dimensions `describe_metric` lists; if one is missing, call `request_metric`.
2. `query` to look at the data (at most 200 rows, for exploring only).
3. Write the spec and the HTML below.
4. `validate_spec` with both, and fix every error. A successful result has a `candidate_id`.
5. `publish_dashboard` with that `candidate_id` (don't send the spec and HTML again). Give the user the URL; the dashboard starts as an uncertified draft.

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
  - A `time_grain` must divide the window's periods: the same grain, `day`, `month` in a quarter or year, `quarter` in a year. Weeks only fit week windows.
- **Interactions**: `{"from": "regions", "on": "click", "sets_filter": "region"}`. A click on a row of `regions` sets the dimension filter `region` to that row's value; clicking it again clears it. The block must have the filter's dimension.
- **Bindings** (`presentation.bindings`): `"#element-id": "block-id"`, documenting which element shows which block. Every block should be bound or drawn with `prumi.on`.

## The page

Your HTML runs in a sandboxed frame. It **can't** call any server (no `fetch`, no images from other hosts) and can't see the page around it. Scripts, stylesheets and fonts may come from `cdn.jsdelivr.net`, `cdnjs.cloudflare.com` or `unpkg.com` (fonts also from Google Fonts), pinned to an exact version (`chart.js@4.4.1`). Prumi adds its runtime to the page; you don't load it.

**Never write numbers into the page**: no datasets, totals or values in the HTML or in script literals, and never compute totals, averages or ratios in JavaScript. Every number comes from `prumi.on`; ask for `total`, `others` or `compare` in the spec instead.

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
- `others` (with `top_n` and `others`) and `total` (with `total`) are objects keyed by metric, or `null` when not asked for or when every group is already in the top N.
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
  "presentation": {"bindings": {"#kpi": "kpi", "#regions": "regions", "#trend": "trend"}}
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
    body { font-family: system-ui, sans-serif; margin: 24px; }
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
            "type": "string"
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
